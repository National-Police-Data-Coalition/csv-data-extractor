from __future__ import annotations

import csv
import json
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from csv_data_extractor import loader_contract as lc
from csv_data_extractor.config import ModelMapping, SOURCE_UID_REQUIRED, SourceConfig
from csv_data_extractor.models import ExtractedItem, ExtractionResult
from csv_data_extractor.transforms import apply_transform, blank_to_none

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class _At:
    """Where a value comes from, so problems become row-level issues, not crashes."""

    result: ExtractionResult
    row_number: int
    model: str
    section: str = "fields"

    def within(self, section: str) -> "_At":
        return _At(self.result, self.row_number, self.model, section)


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
        elif mapping.model == "complaint":
            _extract_complaints(config, mapping, rows, result)
        elif mapping.model == "allegation":
            _extract_allegations(config, mapping, rows, result)
        else:
            result.add_error(f"unsupported model mapping: {mapping.model}")

    _check_allegation_links_in_run(result)
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
        "record_id",
        "complaint_id",
        "value",
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
        at = _At(result, row_number, mapping.model)
        data = _build_dict(row, mapping.fields, config, at)
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
        at = _At(result, row_number, mapping.model)
        data = _build_dict(row, mapping.fields, config, at)
        top_level = _build_dict(row, mapping.top_level, config, at.within("top_level"))
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
        at = _At(result, row_number, mapping.model)
        data = _build_dict(row, mapping.fields, config, at)
        state_id = _build_dict(row, mapping.state_id or {}, config, at.within("state_id"))
        if state_id:
            data["state_ids"] = [state_id]

        if not _validate_required(data, mapping.required, result, row_number, mapping.model):
            continue
        if not _validate_required(
            state_id, ["state", "id_name", "value"], result, row_number, mapping.model
        ):
            continue

        dedupe_key = _resolve_dedupe_key(mapping, data, state_id)
        employment = _build_dict(row, mapping.employment or {}, config, at.within("employment"))
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


def _extract_complaints(
    config: SourceConfig,
    mapping: ModelMapping,
    rows: list[tuple[int, dict[str, str]]],
    result: ExtractionResult,
) -> None:
    """One complaint per record_id, the loader's MERGE key. Allegation-level CSVs
    repeat the complaint columns on every row; the first row wins, and a repeat with
    different values is reported once."""
    complaints: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    first_rows: dict[str, int] = {}
    reported: set[str] = set()

    for row_number, row in rows:
        at = _At(result, row_number, mapping.model)
        data = _build_dict(row, mapping.fields, config, at)
        location = _build_dict(row, mapping.location or {}, config, at.within("location"))
        source_details = _build_dict(
            row, mapping.source_details or {}, config, at.within("source_details")
        )
        if not _validate_required(data, _required(mapping), result, row_number, mapping.model):
            continue
        data["record_id"] = str(data["record_id"])
        if location:
            data["location"] = location
        if source_details:
            data["source_details"] = source_details

        dates_ok = _check_dates(data, lc.COMPLAINT_DATE_FIELDS, at)
        dates_ok &= _check_dates(source_details, lc.SOURCE_DETAILS_DATE_FIELDS,
                                 at.within("source_details"))
        if not (dates_ok and _check_choices(mapping.model, {"source_details": source_details}, at)):
            continue

        key = data["record_id"]
        if key in complaints:
            if complaints[key] != data and key not in reported:
                reported.add(key)
                result.add_warning(
                    f"complaint {key} repeats with different "
                    f"{sorted(_differing_paths(complaints[key], data))}; "
                    f"keeping row {first_rows[key]}",
                    row_number=row_number,
                    model=mapping.model,
                    field="record_id",
                    context={"record_id": key},
                )
            continue
        complaints[key] = data
        first_rows[key] = row_number

    for key, data in complaints.items():
        _append_item(config, mapping, data, result, first_rows[key])


def _extract_allegations(
    config: SourceConfig,
    mapping: ModelMapping,
    rows: list[tuple[int, dict[str, str]]],
    result: ExtractionResult,
) -> None:
    """One allegation per row. complaint_id, officer_state_id and complainant go at
    the TOP level of the item: that is where loader/ingest/allegation.py reads them."""
    first_rows: dict[tuple[str, str], int] = {}

    for row_number, row in rows:
        at = _At(result, row_number, mapping.model)
        data = _build_dict(row, mapping.fields, config, at)
        complaint_id = _resolve_value(
            row, mapping.complaint_id, config, at.within("complaint_id")
        )
        ok = _validate_required(data, _required(mapping), result, row_number, mapping.model)
        if complaint_id is None:
            result.add_error(
                "required field is missing",
                row_number=row_number,
                model=mapping.model,
                field="complaint_id",
            )
            ok = False
        if not ok:
            continue
        data["record_id"] = str(data["record_id"])
        complaint_id = str(complaint_id)
        extra: dict[str, Any] = {"complaint_id": complaint_id}
        context = {"record_id": data["record_id"], "complaint_id": complaint_id}

        # A missing id value is normal (the loader OPTIONAL-matches the officer and the
        # allegation loads unattached); constants without a value are no identifier.
        state_id = _build_dict(
            row, mapping.officer_state_id or {}, config, at.within("officer_state_id")
        )
        if state_id.get("value") is not None:
            if all(state_id.get(f) not in (None, "") for f in lc.STATE_ID_FIELDS):
                extra["officer_state_id"] = {f: str(state_id[f]) for f in lc.STATE_ID_FIELDS}
            else:
                result.add_warning(
                    "officer_state_id is incomplete; omitted, so the allegation loads "
                    "without an officer",
                    row_number=row_number,
                    model=mapping.model,
                    field="officer_state_id",
                    context=context,
                )

        complainant = _build_dict(
            row, mapping.complainant or {}, config, at.within("complainant")
        )
        if "age" in complainant and not isinstance(complainant["age"], int):
            result.add_warning(
                "complainant.age must be an integer (Civilian.age is an IntegerProperty); "
                "use transform: int -- value dropped",
                row_number=row_number,
                model=mapping.model,
                field="complainant.age",
                context=context,
            )
            complainant.pop("age")
        if not _check_choices(mapping.model, {"complainant": complainant}, at):
            continue
        if complainant:
            extra["complainant"] = complainant

        key = (complaint_id, data["record_id"])
        if key in first_rows:
            result.add_warning(
                f"allegation {data['record_id']} of complaint {complaint_id} repeats "
                f"(first on row {first_rows[key]}); the loader merges them and the last "
                "row wins",
                row_number=row_number,
                model=mapping.model,
                field="record_id",
                context=context,
            )
        else:
            first_rows[key] = row_number
        _append_item(config, mapping, data, result, row_number, **extra)


def _required(mapping: ModelMapping) -> list[str]:
    return list(dict.fromkeys(["record_id", *mapping.required]))


def _check_dates(container: dict[str, Any], fields: Iterable[str], at: _At) -> bool:
    """DateProperty fields must already be YYYY-MM-DD: neomodel inflates them with
    strptime("%Y-%m-%d"), so any other shape loads and then raises on every read."""
    ok = True
    for field in fields:
        value = container.get(field)
        if value is None:
            continue
        if not (isinstance(value, str) and ISO_DATE.fullmatch(value) and _parse_iso_date(value)):
            at.result.add_error(
                "not a YYYY-MM-DD date, which the loader's DateProperty needs; "
                "add transform: date",
                row_number=at.row_number,
                model=at.model,
                field=_path(at, field),
                context={"value": str(value)},
            )
            ok = False
    return ok


def _check_choices(model: str, sections: dict[str, dict[str, Any]], at: _At) -> bool:
    """Values outside a choices= list write fine and raise when neomodel reads them."""
    ok = True
    for (choice_model, path), allowed in lc.CHOICES.items():
        if choice_model != model:
            continue
        section, field = path.split(".", 1)
        value = (sections.get(section) or {}).get(field)
        if value is not None and value not in allowed:
            at.result.add_error(
                f"not one of the loader's choices {list(allowed)}; it would load and then "
                "raise on read -- map it, or set unmapped: null",
                row_number=at.row_number,
                model=model,
                field=path,
                context={"value": str(value)},
            )
            ok = False
    return ok


def _differing_paths(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> list[str]:
    paths = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            paths.extend(_differing_paths(a, b, f"{prefix}{key}."))
        elif a != b:
            paths.append(f"{prefix}{key}")
    return paths


def _check_allegation_links_in_run(result: ExtractionResult) -> None:
    """When a run emits complaints, every allegation it emits should name one of them:
    the loader resolves the parent with a hard MATCH and silently drops the rest."""
    complaint_ids = {item.data.get("record_id") for item in result.items
                     if item.model == "complaint"}
    if not complaint_ids:
        return
    missing: Counter = Counter()
    for item in result.items:
        if item.model == "allegation":
            complaint_id = (item.model_extra or {}).get("complaint_id")
            if complaint_id not in complaint_ids:
                missing[complaint_id] += 1
    for complaint_id, count in sorted(missing.items()):
        result.add_warning(
            f"{count} allegation(s) name complaint {complaint_id}, which this run did not "
            "emit; the loader drops them unless that complaint is already in the graph",
            model="allegation",
            field="complaint_id",
            context={"complaint_id": complaint_id},
        )


def check_links(paths: Iterable[str | Path]) -> tuple[ExtractionResult, dict[str, int]]:
    """Cross-file check over loader JSONL: does every allegation resolve to a complaint
    (same source_uid and record_id; the loader's complaint key covers both) and, when
    officer files are included, to an officer? Two streaming passes, so the files can
    come in any order."""
    paths = [Path(p) for p in paths]
    result = ExtractionResult()
    stats: Counter = Counter()
    complaints: set[tuple[str, str]] = set()
    officers: set[tuple[str, str, str]] = set()

    for path, line_number, obj in _iter_jsonl(paths):
        model = obj.get("model", "_missing")
        stats[f"{model} records"] += 1
        data = obj.get("data") or {}
        if model == "complaint":
            key = (obj.get("source_uid"), data.get("record_id"))
            if key in complaints:
                result.add_warning(
                    f"complaint {key[1]} appears more than once for source {key[0]}",
                    row_number=line_number,
                    model=model,
                    field="record_id",
                    context={"record_id": key[1]},
                )
            complaints.add(key)
        elif model == "officer":
            for sid in data.get("state_ids") or []:
                officers.add((sid.get("state"), sid.get("id_name"), sid.get("value")))

    missing_complaints: Counter = Counter()
    unresolved_officers: Counter = Counter()
    seen_allegations: set[tuple[str, str, str]] = set()
    for path, line_number, obj in _iter_jsonl(paths):
        if obj.get("model") != "allegation":
            continue
        source_uid = obj.get("source_uid")
        complaint_id = obj.get("complaint_id")
        record_id = (obj.get("data") or {}).get("record_id")
        key = (source_uid, complaint_id, record_id)
        if key in seen_allegations:
            result.add_warning(
                f"allegation {record_id} of complaint {complaint_id} appears more than once",
                row_number=line_number,
                model="allegation",
                field="record_id",
                context={"record_id": record_id, "complaint_id": complaint_id},
            )
        seen_allegations.add(key)
        if (source_uid, complaint_id) in complaints:
            stats["allegations linked to a complaint"] += 1
        else:
            missing_complaints[(source_uid, complaint_id)] += 1
        sid = obj.get("officer_state_id")
        if not sid:
            stats["allegations without an officer id"] += 1
        elif not officers:
            stats["officer links not checked (no officer records given)"] += 1
        elif (sid.get("state"), sid.get("id_name"), sid.get("value")) in officers:
            stats["allegations linked to an officer"] += 1
        else:
            unresolved_officers[(sid.get("state"), sid.get("id_name"), sid.get("value"))] += 1

    for (source_uid, complaint_id), count in sorted(missing_complaints.items(), key=str):
        stats["allegations with no complaint"] += count
        result.add_error(
            f"{count} allegation(s) name complaint {complaint_id} (source {source_uid}), "
            "which none of these files contains; the loader drops them silently unless "
            "the complaint is already in the graph under the same source",
            model="allegation",
            field="complaint_id",
            context={"complaint_id": complaint_id},
        )
    for (state, id_name, value), count in sorted(unresolved_officers.items(), key=str):
        stats["allegations whose officer is not in these files"] += count
        result.add_warning(
            f"{count} allegation(s) name officer {state}/{id_name}/{value}, who is not in "
            "these files; they load unattached unless the officer is already in the graph",
            model="allegation",
            field="officer_state_id",
        )
    return result, dict(stats)


def _iter_jsonl(paths: list[Path]):
    for path in paths:
        with path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    yield path, line_number, json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{path}:{line_number} is not loader JSONL ({exc.msg})"
                    ) from exc


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
    at: _At | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, spec in specs.items():
        value = _resolve_value(row, spec, config, at, field)
        if value is not None:
            out[field] = value
    return out


def _resolve_value(
    row: dict[str, str],
    spec: Any,
    config: SourceConfig,
    at: _At | None = None,
    field: str = "",
) -> Any:
    """column (or first non-blank of a column list) -> default -> transforms ->
    map / contains. A transform that cannot convert a value is a row-level warning
    and drops the value, so one bad cell cannot crash a run."""
    if isinstance(spec, str):
        if spec.startswith("$"):
            return _resolve_reference(spec, config)
        return blank_to_none(row.get(spec))

    if not isinstance(spec, dict):
        return blank_to_none(spec)

    if "value" in spec:
        value = _resolve_reference(spec["value"], config) if isinstance(spec["value"], str) else spec["value"]
    else:
        value = _first_value(row, spec.get("column"))
        if value is None and "default" in spec:
            default = spec["default"]
            value = _resolve_reference(default, config) if isinstance(default, str) else default

    transforms = spec.get("transform", [])
    if isinstance(transforms, str):
        transforms = [transforms]
    for transform in transforms:
        if value is None:
            break
        try:
            value = apply_transform(transform, value)
        except (ValueError, TypeError, OverflowError):
            if at is None:
                raise
            at.result.add_warning(
                f"transform {transform!r} could not convert the value; value dropped",
                row_number=at.row_number,
                model=at.model,
                field=_path(at, field),
                context={"value": str(value)},
            )
            return None
    value = blank_to_none(value)
    if value is not None and ("map" in spec or "contains" in spec):
        value = _map_value(value, spec, at, field)
    return value


def _first_value(row: dict[str, str], column: Any) -> Any:
    if not column:
        return None
    for name in [column] if isinstance(column, str) else column:
        value = blank_to_none(row.get(name))
        if value is not None:
            return value
    return None


_LOOKUPS: dict[int, tuple[dict, tuple[dict[str, Any], list[tuple[str, Any]]]]] = {}


def _lookups(spec: dict) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Case-insensitive `map` table and ordered `contains` rules, built once per spec."""
    cached = _LOOKUPS.get(id(spec))
    if cached is not None and cached[0] is spec:
        return cached[1]
    exact = {str(key).strip().casefold(): out for key, out in (spec.get("map") or {}).items()}
    rules = [(str(needle).casefold(), out) for needle, out in (spec.get("contains") or [])]
    _LOOKUPS[id(spec)] = (spec, (exact, rules))
    return exact, rules


def _map_value(value: Any, spec: dict, at: _At | None, field: str) -> Any:
    exact, rules = _lookups(spec)
    text = str(value).strip().casefold()
    if text in exact:
        return blank_to_none(exact[text])
    for needle, out in rules:
        if needle in text:
            return blank_to_none(out)
    policy = spec.get("unmapped", "keep")
    if policy == "keep":
        return value
    if policy in ("warn", "error") and at is not None:
        report = at.result.add_warning if policy == "warn" else at.result.add_error
        report(
            "no mapping for the value; value dropped",
            row_number=at.row_number,
            model=at.model,
            field=_path(at, field),
            context={"value": str(value)},
        )
    return None


def _path(at: _At, field: str) -> str:
    if at.section == "fields":
        return field
    return f"{at.section}.{field}" if field else at.section


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
