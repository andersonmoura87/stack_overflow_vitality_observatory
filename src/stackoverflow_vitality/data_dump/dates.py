"""UTC timestamps accepted from a Data Dump attribute or operator cutoff."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_INSTANT = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?$")
EARLIEST = datetime(2008, 1, 1, tzinfo=UTC)


def parse_dump_instant(value: str) -> datetime | None:
    match = _INSTANT.fullmatch(value)
    if match is None:
        return None
    try:
        parsed = datetime.fromisoformat(f"{match.group(1)}{match.group(2) or ''}").replace(
            tzinfo=UTC
        )
    except ValueError:
        return None
    return parsed


def normalize_cutoff(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("dump_cutoff must be a UTC timestamp")
    text = value[:-1] if value.endswith("Z") else value
    if parse_dump_instant(text) is None:
        raise ValueError("dump_cutoff must be a UTC timestamp")
    return f"{text}Z"


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
