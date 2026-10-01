from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from csv_data_extractor import loader_contract as lc
from csv_data_extractor.models import SUPPORTED_MODELS
from csv_data_extractor.transforms import BUILTIN_TRANSFORMS

SOURCE_UID_REQUIRED = "__SOURCE_UID_REQUIRED__"

SPEC_KEYS = {"column", "value", "default", "transform", "map", "contains", "unmapped"}
UNMAPPED_POLICIES = ("keep", "null", "warn", "error")

# Mapping keys that only make sense for one model.
MODEL_ONLY_KEYS = {
    "location": "complaint",
    "source_details": "complaint",
    "complaint_id": "allegation",
    "officer_state_id": "allegation",
    "complainant": "allegation",
}


class SourceMetadata(BaseModel):
    uid: str
    name: str
    url: str


class Defaults(BaseModel):
    state: str | None = None
    url: str | None = None
    scraped_at_timezone: str = "UTC"


class ModelMapping(BaseModel):
    model: Literal["agency", "unit", "officer", "complaint", "allegation"]
    fields: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    state_id: dict[str, Any] | None = None
    employment: dict[str, Any] | None = None
    employment_mode: Literal["events", "stints"] = "events"
    top_level: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None
    # complaint: nested sections the loader reads from data.location / data.source_details
    location: dict[str, Any] | None = None
    source_details: dict[str, Any] | None = None
    # allegation: read by the loader at the TOP level of the item, beside `data`
    complaint_id: Any = None
    officer_state_id: dict[str, Any] | None = None
    complainant: dict[str, Any] | None = None

    @field_validator("model")
    @classmethod
    def supported_model(cls, value: str) -> str:
        if value not in SUPPORTED_MODELS:
            raise ValueError(f"unsupported model: {value}")
        return value

    @model_validator(mode="after")
    def follows_loader_contract(self) -> "ModelMapping":
        problems: list[str] = []
        for key, owner in MODEL_ONLY_KEYS.items():
            if getattr(self, key) is not None and self.model != owner:
                problems.append(f"`{key}` is only valid on {owner} mappings")
        for section, specs in self._spec_sections():
            for field, spec in specs.items():
                problems.extend(_spec_problems(spec, f"{section}.{field}".rstrip(".")))
        if self.model == "complaint":
            problems.extend(self._complaint_problems())
        elif self.model == "allegation":
            problems.extend(self._allegation_problems())
        if problems:
            raise ValueError(f"{self.model} mapping: " + "; ".join(problems))
        return self

    def _spec_sections(self) -> list[tuple[str, dict[str, Any]]]:
        sections = [("fields", self.fields), ("top_level", self.top_level)]
        for name in ("state_id", "employment", "location", "source_details",
                     "officer_state_id", "complainant"):
            value = getattr(self, name)
            if value is not None:
                sections.append((name, value))
        if self.complaint_id is not None:
            sections.append(("complaint_id", {"": self.complaint_id}))
        return sections

    def _common_problems(self, readable: tuple[str, ...]) -> list[str]:
        """Checks shared by the complaint and allegation mappings."""
        problems = []
        for key in ("state_id", "employment"):
            if getattr(self, key) is not None:
                problems.append(f"`{key}` is only valid on officer mappings")
        if self.top_level:
            problems.append("`top_level` is not read for this model; use its explicit keys")
        if "record_id" not in self.fields:
            problems.append("fields.record_id is required (the loader drops rows without it)")
        unknown = sorted(set(self.fields) - {"record_id", *readable})
        if unknown:
            problems.append(f"fields not read by the loader: {unknown}")
        unmapped_required = sorted(set(self.required) - set(self.fields))
        if unmapped_required:
            problems.append(f"required lists fields that are not mapped: {unmapped_required}")
        return problems

    def _complaint_problems(self) -> list[str]:
        problems = []
        trapped = sorted(set(self.fields) & set(lc.COMPLAINT_LIST_FIELDS))
        if trapped:
            problems.append(
                f"fields {trapped} are list-valued in the loader and fail the whole batch; "
                "they cannot be mapped")
        problems.extend(self._common_problems(lc.COMPLAINT_FIELDS))
        unknown = sorted(set(self.location or {}) - set(lc.LOCATION_FIELDS))
        if unknown:
            problems.append(f"location fields not read by the loader: {unknown}")
        unknown = sorted(set(self.source_details or {}) - set(lc.SOURCE_DETAILS_FIELDS))
        if unknown:
            problems.append(f"source_details fields not read by the loader: {unknown}")
        if "record_type" not in (self.source_details or {}):
            problems.append(
                "source_details.record_type is required: the domain's "
                "ComplaintSourceRel.record_type is required, with choices "
                f"{list(lc.RECORD_TYPE_CHOICES)}")
        if self.dedupe_key not in (None, "record_id"):
            problems.append("complaints dedupe on record_id (the loader's MERGE key); "
                            "remove dedupe_key")
        return problems

    def _allegation_problems(self) -> list[str]:
        problems = self._common_problems(lc.ALLEGATION_FIELDS)
        if self.complaint_id is None:
            problems.append("complaint_id is required (the parent complaint's record_id)")
        if self.officer_state_id is not None:
            unknown = sorted(set(self.officer_state_id) - set(lc.STATE_ID_FIELDS))
            missing = sorted(set(lc.STATE_ID_FIELDS) - set(self.officer_state_id))
            if unknown:
                problems.append(f"officer_state_id fields not read by the loader: {unknown}")
            if missing:
                problems.append(f"officer_state_id needs {list(lc.STATE_ID_FIELDS)}; "
                                f"missing {missing}")
        unknown = sorted(set(self.complainant or {}) - set(lc.CIVILIAN_FIELDS))
        if unknown:
            problems.append(f"complainant fields not read by the loader: {unknown}")
        if self.dedupe_key is not None:
            problems.append("allegations are one per row; remove dedupe_key")
        return problems


def _spec_problems(spec: Any, where: str) -> list[str]:
    """Structural checks on one field spec: a column name, a constant, or a dict."""
    if not isinstance(spec, dict):
        return []
    problems = []
    unknown = sorted(set(spec) - SPEC_KEYS)
    if unknown:
        problems.append(f"{where}: unknown spec keys {unknown}")
    column = spec.get("column")
    if column is not None and not (
        isinstance(column, str)
        or (isinstance(column, list) and column and all(isinstance(c, str) for c in column))
    ):
        problems.append(f"{where}: column must be a name or a non-empty list of names")
    transforms = spec.get("transform", [])
    for name in [transforms] if isinstance(transforms, str) else transforms:
        if name not in BUILTIN_TRANSFORMS:
            problems.append(f"{where}: unknown transform {name!r}")
    if "map" in spec and not isinstance(spec["map"], dict):
        problems.append(f"{where}: map must be a mapping of source value -> output value")
    if "contains" in spec and not (
        isinstance(spec["contains"], list)
        and all(isinstance(rule, list) and len(rule) == 2 and isinstance(rule[0], str)
                for rule in spec["contains"])
    ):
        problems.append(f"{where}: contains must be a list of [substring, output] pairs")
    if "unmapped" in spec:
        if spec["unmapped"] not in UNMAPPED_POLICIES:
            problems.append(f"{where}: unmapped must be one of {list(UNMAPPED_POLICIES)}")
        if "map" not in spec and "contains" not in spec:
            problems.append(f"{where}: unmapped only applies with map or contains")
    return problems


class SourceConfig(BaseModel):
    source: SourceMetadata
    defaults: Defaults = Field(default_factory=Defaults)
    mappings: list[ModelMapping]

    def with_source_uid(self, source_uid: str | None) -> "SourceConfig":
        if not source_uid:
            return self
        return self.model_copy(
            update={"source": self.source.model_copy(update={"uid": source_uid})}
        )


def load_source_config(path: str | Path) -> SourceConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return SourceConfig.model_validate(raw)
