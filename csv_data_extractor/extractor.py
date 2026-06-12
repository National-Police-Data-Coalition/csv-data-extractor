from __future__ import annotations

import csv
import json
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from csv_data_extractor.config import ModelMapping, SourceConfig
from csv_data_extractor.models import ExtractedItem, ExtractionResult
from csv_data_extractor.transforms import apply_transform, blank_to_none


def extract_csv(config: SourceConfig, csv_path: str | Path) -> ExtractionResult:
    result = ExtractionResult()
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


def summarize_jsonl(path: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
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
        if employment:
            if _employment_is_loader_usable(employment):
                officers[dedupe_key]["employment"].append(employment)
            else:
                result.add_warning(
                    "employment skipped because agency/unit labels or states are missing",
                    row_number=row_number,
                    model=mapping.model,
                    field="employment",
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
