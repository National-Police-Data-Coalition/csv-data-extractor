from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LOADER_SCRAPED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"
SUPPORTED_MODELS = {"agency", "unit", "officer"}


class Issue(BaseModel):
    severity: Literal["error", "warning"]
    message: str
    row_number: int | None = None
    model: str | None = None
    field: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: Literal["agency", "unit", "officer"]
    source_uid: str
    url: str
    data: dict[str, Any]
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source_uid", "url")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    def to_loader_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        scraped_at = self.scraped_at
        if scraped_at.tzinfo is not None:
            scraped_at = scraped_at.astimezone(UTC).replace(tzinfo=None)
        payload["scraped_at"] = scraped_at.strftime(LOADER_SCRAPED_AT_FORMAT)
        return payload


class ExtractionResult(BaseModel):
    items: list[ExtractedItem] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def add_error(
        self,
        message: str,
        *,
        row_number: int | None = None,
        model: str | None = None,
        field: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            Issue(
                severity="error",
                message=message,
                row_number=row_number,
                model=model,
                field=field,
                context=context or {},
            )
        )

    def add_warning(
        self,
        message: str,
        *,
        row_number: int | None = None,
        model: str | None = None,
        field: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            Issue(
                severity="warning",
                message=message,
                row_number=row_number,
                model=model,
                field=field,
                context=context or {},
            )
        )
