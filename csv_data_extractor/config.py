from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from csv_data_extractor.models import SUPPORTED_MODELS


class SourceMetadata(BaseModel):
    uid: str
    name: str
    url: str


class Defaults(BaseModel):
    state: str | None = None
    url: str | None = None
    scraped_at_timezone: str = "UTC"


class ModelMapping(BaseModel):
    model: Literal["agency", "unit", "officer"]
    fields: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    state_id: dict[str, Any] | None = None
    employment: dict[str, Any] | None = None
    top_level: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None

    @field_validator("model")
    @classmethod
    def supported_model(cls, value: str) -> str:
        if value not in SUPPORTED_MODELS:
            raise ValueError(f"unsupported model: {value}")
        return value


class SourceConfig(BaseModel):
    source: SourceMetadata
    defaults: Defaults = Field(default_factory=Defaults)
    mappings: list[ModelMapping]


def load_source_config(path: str | Path) -> SourceConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return SourceConfig.model_validate(raw)
