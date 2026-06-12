from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from dateutil import parser


def blank_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def as_title(value: Any) -> Any:
    value = blank_to_none(value)
    return value.title() if isinstance(value, str) else value


def as_upper(value: Any) -> Any:
    value = blank_to_none(value)
    return value.upper() if isinstance(value, str) else value


def as_int(value: Any) -> int | None:
    value = blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return int(float(str(value)))


def as_date(value: Any) -> str | None:
    value = blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return parser.parse(str(value), fuzzy=True).date().isoformat()


def strip(value: Any) -> Any:
    return blank_to_none(value)


BUILTIN_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "date": as_date,
    "int": as_int,
    "strip": strip,
    "title": as_title,
    "upper": as_upper,
}


def apply_transform(name: str, value: Any) -> Any:
    try:
        transform = BUILTIN_TRANSFORMS[name]
    except KeyError as exc:
        raise ValueError(f"unknown transform: {name}") from exc
    return transform(value)
