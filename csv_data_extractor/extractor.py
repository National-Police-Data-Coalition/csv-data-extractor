from __future__ import annotations

import csv
import json
from collections import Counter, OrderedDict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from csv_data_extractor.config import ModelMapping, SOURCE_UID_REQUIRED, SourceConfig
from csv_data_extractor.models import ExtractedItem, ExtractionResult
from csv_data_extractor.transforms import apply_transform, blank_to_none


def extract_csv(config: SourceConfig, csv_path: str | Path) -> ExtractionResult:
    result = ExtractionResult()
    if config.source.uid == SOURCE_UID_REQUIRED:
        result.add_error(
            "source uid must be provided with --source-uid",
            field="source.uid",
        )
        return result

    rows = _read_csv(csv_path)

    for mapping in config.mappings:
        if mapping.model == "agency":
            _extract_agencies(config, mapping, rows, result)
        elif mapping.model == "unit":
            _extract_units(config, mapping, rows, result)
        elif mapping.model == "officer":
            _extract_officers(config, mapping, rows, result)
        else:
            result.add_error(f"unsupported model mapping: {mapping.model}")

    return result


def write_jsonl(items: Iterable[ExtractedItem], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for item in items:
            f.write(json.dumps(item.to_loader_dict(), ensure_ascii=False, sort_keys=True))
            f.write("\n")


def write_issues_csv(issues: Iterable, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    context_fields = (
        "person_nbr",
        "document_id",
        "full_name",
        "agency_name",
        "earliest_date",
        "latest_date",
        "employment_status",
        "status",
    )
    fieldnames = (
        "severity",
        "row_number",
        "model",
        "field",
        "message",
        *context_fields,
    )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for issue in issues:
            row = {
                "severity": issue.severity,
                "row_number": issue.row_number,
                "model": issue.model,
                "field": issue.field,
                "message": issue.message,
            }
            row.update({key: issue.context.get(key) for key in context_fields})
            writer.writerow(row)


def summarize_jsonl(path: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with Path(path).open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "summarize expects loader JSONL, not source CSV. "
                    f"Could not parse {path} as JSONL at line {line_number}."
                ) from exc
            model = obj.get("model", "_missing")
            counts[model] = counts.get(model, 0) + 1
    return counts


def _read_csv(path: str | Path) -> list[tuple[int, dict[str, str]]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [(line_number, row) for line_number, row in enumerate(reader, start=2)]


def _extract_agencies(
    config: SourceConfig,
    mapping: ModelMapping,
    rows: list[tuple[int, dict[str, str]]],
    result: ExtractionResult,
) -> None:
    seen: set[tuple[Any, Any]] = set()
    for row_number, row in rows:
        data = _build_dict(row, mapping.fields, config)
        if not _validate_required(data, mapping.required, result, row_number, mapping.model):
            continue
        key = (data.get("name"), data.get("hq_state"))
        if key in seen:
            continue
        seen.add(key)
        _append_item(config, mapping, data, result, row_number)


def _extract_units(
    config: SourceConfig,
    mapping: ModelMapping,
    rows: list[tuple[int, dict[str, str]]],
    result: ExtractionResult,
) -> None:
    seen: set[tuple[Any, Any, Any]] = set()
    for row_number, row in rows:
        data = _build_dict(row, mapping.fields, config)
        top_level = _build_dict(row, mapping.top_level, config)
        if not _validate_required(data, mapping.required, result, row_number, mapping.model):
            continue
        if not _validate_required(
            top_level, ["agency", "a_hq_state"], result, row_number, mapping.model
        ):
            continue
        key = (top_level.get("agency"), top_level.get("a_hq_state"), data.get("name"))
        if key in seen:
            continue
        seen.add(key)
        _append_item(config, mapping, data, result, row_number, **top_level)


def _extract_officers(
    config: SourceConfig,
    mapping: ModelMapping,
    rows: list[tuple[int, dict[str, str]]],
    result: ExtractionResult,
) -> None:
    officers: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    row_numbers: dict[str, int] = {}
    employment_events: dict[str, list[tuple[int, dict[str, str], dict[str, Any]]]] = {}

    for row_number, row in rows:
        data = _build_dict(row, mapping.fields, config)
        state_id = _build_dict(row, mapping.state_id or {}, config)
        if state_id:
            data["state_ids"] = [state_id]

        if not _validate_required(data, mapping.required, result, row_number, mapping.model):
            continue
        if not _validate_required(
            state_id, ["state", "id_name", "value"], result, row_number, mapping.model
        ):
            continue

        dedupe_key = _resolve_dedupe_key(mapping, data, state_id)
        employment = _build_dict(row, mapping.employment or {}, config)
        employment = {k: v for k, v in employment.items() if v is not None}

        if dedupe_key not in officers:
            officers[dedupe_key] = {"data": data, "employment": []}
            row_numbers[dedupe_key] = row_number
            employment_events[dedupe_key] = []
        if employment:
            if _employment_is_loader_usable(employment):
                if mapping.employment_mode == "stints":
                    employment_events[dedupe_key].append((row_number, row, employment))
                else:
                    _warn_about_employment_dates(employment, result, row_number, row)
                    officers[dedupe_key]["employment"].append(employment)
            else:
                result.add_warning(
                    "employment skipped because agency/unit labels or states are missing",
                    row_number=row_number,
                    model=mapping.model,
                    field="employment",
                )

    if mapping.employment_mode == "stints":
        for dedupe_key, events in employment_events.items():
            officers[dedupe_key]["employment"] = _build_employment_stints(
                events,
                result,
            )

    for dedupe_key, payload in officers.items():
        _append_item(
            config,
            mapping,
            payload["data"],
            result,
            row_numbers[dedupe_key],
            employment=payload["employment"],
        )


def _append_item(
    config: SourceConfig,
    mapping: ModelMapping,
    data: dict[str, Any],
    result: ExtractionResult,
    row_number: int,
    **extra: Any,
) -> None:
    try:
        item = ExtractedItem(
            model=mapping.model,
            source_uid=config.source.uid,
            url=_source_url(config, mapping),
            data=data,
            scraped_at=datetime.now(UTC),
            **{k: v for k, v in extra.items() if v is not None},
        )
    except ValidationError as exc:
        result.add_error(str(exc), row_number=row_number, model=mapping.model)
        return
    result.items.append(item)


def _build_dict(
    row: dict[str, str],
    specs: dict[str, Any],
    config: SourceConfig,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, spec in specs.items():
        value = _resolve_value(row, spec, config)
        if value is not None:
            out[field] = value
    return out


def _resolve_value(row: dict[str, str], spec: Any, config: SourceConfig) -> Any:
    if isinstance(spec, str):
        if spec.startswith("$"):
            return _resolve_reference(spec, config)
        return blank_to_none(row.get(spec))

    if not isinstance(spec, dict):
        return blank_to_none(spec)

    if "value" in spec:
        value = _resolve_reference(spec["value"], config) if isinstance(spec["value"], str) else spec["value"]
    else:
        column = spec.get("column")
        value = blank_to_none(row.get(column)) if column else None
        if value is None and "default" in spec:
            default = spec["default"]
            value = _resolve_reference(default, config) if isinstance(default, str) else default

    transforms = spec.get("transform", [])
    if isinstance(transforms, str):
        transforms = [transforms]
    for transform in transforms:
        if value is None:
            break
        value = apply_transform(transform, value)
    return blank_to_none(value)


def _resolve_reference(value: str, config: SourceConfig) -> Any:
    if value == "$source.uid":
        return config.source.uid
    if value == "$source.url":
        return config.source.url
    if value == "$defaults.state":
        return config.defaults.state
    if value == "$defaults.url":
        return config.defaults.url
    return value


def _source_url(config: SourceConfig, mapping: ModelMapping) -> str:
    if config.defaults.url:
        return config.defaults.url
    return config.source.url


def _validate_required(
    data: dict[str, Any],
    fields: list[str],
    result: ExtractionResult,
    row_number: int,
    model: str,
) -> bool:
    ok = True
    for field in fields:
        if data.get(field) in (None, ""):
            result.add_error(
                "required field is missing",
                row_number=row_number,
                model=model,
                field=field,
            )
            ok = False
    return ok


def _resolve_dedupe_key(
    mapping: ModelMapping,
    data: dict[str, Any],
    state_id: dict[str, Any],
) -> str:
    if mapping.dedupe_key:
        return str(data.get(mapping.dedupe_key) or state_id.get(mapping.dedupe_key))
    return f"{state_id.get('state')}:{state_id.get('id_name')}:{state_id.get('value')}"


def _employment_is_loader_usable(employment: dict[str, Any]) -> bool:
    return all(
        employment.get(field)
        for field in ("agency_label", "a_hq_state", "unit_label", "u_hq_state")
    )


def _build_employment_stints(
    events: list[tuple[int, dict[str, str], dict[str, Any]]],
    result: ExtractionResult,
) -> list[dict[str, Any]]:
    groups: "OrderedDict[tuple[Any, ...], list[tuple[int, dict[str, str], dict[str, Any]]]]" = OrderedDict()
    for row_number, row, employment in events:
        repaired = _repair_employment_event_dates(employment)
        key = (
            repaired.get("agency_label"),
            repaired.get("a_hq_state"),
            repaired.get("unit_label"),
            repaired.get("u_hq_state"),
            repaired.get("highest_rank"),
        )
        groups.setdefault(key, []).append((row_number, row, repaired))

    stints: list[dict[str, Any]] = []
    for group_events in groups.values():
        stints.append(_merge_employment_group(group_events, result))
    return stints


def _repair_employment_event_dates(employment: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(employment)
    earliest = _parse_iso_date(repaired.get("earliest_date"))
    latest = _parse_iso_date(repaired.get("latest_date"))
    today = datetime.now(UTC).date()

    if earliest and earliest > today:
        shifted = _shift_year(earliest, -60)
        if shifted and (latest is None or shifted <= latest):
            earliest = shifted
        else:
            earliest = None

    if latest and latest > today:
        shifted = _shift_year(latest, -10)
        if shifted and shifted <= today and (earliest is None or shifted >= earliest):
            latest = shifted
        else:
            latest = None

    if earliest and latest and earliest > latest:
        if earliest == date(2000, 1, 1):
            earliest = None
        else:
            shifted = _shift_year(earliest, -60)
            if shifted and shifted <= latest:
                earliest = shifted
            else:
                earliest = None

    status = str(repaired.get("status") or "").lower()
    if status and status != "active" and latest is None and earliest is not None:
        latest = earliest
        earliest = None

    if earliest:
        repaired["earliest_date"] = earliest.isoformat()
    else:
        repaired.pop("earliest_date", None)

    if latest:
        repaired["latest_date"] = latest.isoformat()
    else:
        repaired.pop("latest_date", None)

    return repaired


def _merge_employment_group(
    events: list[tuple[int, dict[str, str], dict[str, Any]]],
    result: ExtractionResult,
) -> dict[str, Any]:
    dated_events = sorted(
        events,
        key=lambda event: (
            _parse_iso_date(event[2].get("latest_date"))
            or _parse_iso_date(event[2].get("earliest_date"))
            or date.min,
            event[0],
        ),
    )
    employments = [event[2] for event in dated_events]
    base = {
        "agency_label": employments[0].get("agency_label"),
        "a_hq_state": employments[0].get("a_hq_state"),
        "unit_label": employments[0].get("unit_label"),
        "u_hq_state": employments[0].get("u_hq_state"),
        "highest_rank": employments[0].get("highest_rank"),
        "rank_label": employments[0].get("rank_label"),
    }

    starts = [
        parsed
        for parsed in (_parse_iso_date(emp.get("earliest_date")) for emp in employments)
        if parsed is not None
    ]
    ends = [
        parsed
        for parsed in (_parse_iso_date(emp.get("latest_date")) for emp in employments)
        if parsed is not None
    ]

    if starts:
        base["earliest_date"] = min(starts).isoformat()

    is_active = any(str(emp.get("status") or "").lower() == "active" for emp in employments)
    if not is_active and ends:
        base["latest_date"] = max(ends).isoformat()

    for field in ("type", "status", "change"):
        value = _choose_stint_value(employments, field)
        if value is not None:
            base[field] = value

    row_number, row, _ = dated_events[-1]
    _warn_about_employment_dates(base, result, row_number, row)
    return {k: v for k, v in base.items() if v is not None}


def _choose_stint_value(employments: list[dict[str, Any]], field: str) -> Any:
    values = [emp.get(field) for emp in employments if emp.get(field)]
    if not values:
        return None
    if field == "status" and any(str(value).lower() == "active" for value in values):
        return next(value for value in reversed(values) if str(value).lower() == "active")
    return Counter(values).most_common(1)[0][0]


def _shift_year(value: date, years: int) -> date | None:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return None


def _warn_about_employment_dates(
    employment: dict[str, Any],
    result: ExtractionResult,
    row_number: int,
    row: dict[str, str],
) -> None:
    earliest = _parse_iso_date(employment.get("earliest_date"))
    latest = _parse_iso_date(employment.get("latest_date"))
    today = datetime.now(UTC).date()
    context = _issue_context(row, employment)

    if earliest and earliest > today:
        result.add_warning(
            "employment start date is in the future",
            row_number=row_number,
            model="officer",
            field="employment.earliest_date",
            context=context,
        )
    if latest and latest > today:
        result.add_warning(
            "employment end date is in the future",
            row_number=row_number,
            model="officer",
            field="employment.latest_date",
            context=context,
        )
    if earliest and latest and earliest > latest:
        result.add_warning(
            "employment start date is after end date",
            row_number=row_number,
            model="officer",
            field="employment",
            context=context,
        )

    status = employment.get("status")
    if status and status.lower() != "active" and latest is None:
        result.add_warning(
            "non-active employment is missing an end date",
            row_number=row_number,
            model="officer",
            field="employment.latest_date",
            context=context,
        )


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _issue_context(
    row: dict[str, str],
    employment: dict[str, Any],
) -> dict[str, Any]:
    keys = (
        "person_nbr",
        "document_id",
        "full_name",
        "agency_name",
        "status",
        "employment_status",
    )
    context = {key: row[key] for key in keys if row.get(key)}
    for key in ("earliest_date", "latest_date"):
        if employment.get(key):
            context[key] = employment[key]
    return context
