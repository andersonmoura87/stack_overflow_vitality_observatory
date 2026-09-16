"""Strict YAML configuration and explicit environment/CLI precedence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from stackoverflow_vitality.domain.models import CollectionRequest, CollectionWindow


@dataclass(frozen=True)
class CollectionConfig:
    site: str = "stackoverflow"
    endpoint: str = "https://api.stackexchange.com/2.3/questions"
    tag: str = ""
    window_start: str = ""
    window_end: str = ""
    page_size: int = 100
    max_pages: int = 25
    overlap_seconds: int = 86400
    api_filter: str = "default"
    user_agent: str = "stackoverflow-vitality-observatory/0.2"
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_attempts: int = 4
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    jitter: float = 1.0
    output: str = "data/bronze"
    backfill: bool = False

    def request(self) -> CollectionRequest:
        for field in fields(self):
            value = getattr(self, field.name)
            default = getattr(CollectionConfig(), field.name)
            if isinstance(default, bool):
                valid = type(value) is bool
            elif isinstance(default, int):
                valid = type(value) is int
            elif isinstance(default, float):
                valid = type(value) in (int, float) and math.isfinite(value)
            else:
                valid = isinstance(value, str) and bool(value.strip())
            if not valid:
                raise ValueError(f"invalid configuration field: {field.name}")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("timeouts must be positive")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between zero and one")
        if not 0 <= self.initial_backoff_seconds <= self.max_backoff_seconds:
            raise ValueError("retry delays must satisfy 0 <= initial <= maximum")
        if any(ord(char) < 32 or ord(char) > 126 for char in self.user_agent):
            raise ValueError("User-Agent must contain printable ASCII only")
        if "\x00" in self.output:
            raise ValueError("invalid output directory")
        output = Path(self.output)
        if any(path.exists() and not path.is_dir() for path in (output, *output.parents)):
            raise ValueError("output must be a directory, with directory ancestors")
        try:
            window = CollectionWindow(
                datetime.fromisoformat(self.window_start.replace("Z", "+00:00")),
                datetime.fromisoformat(self.window_end.replace("Z", "+00:00")),
            )
        except ValueError:
            raise ValueError("invalid window: use ordered ISO timestamps with timezone") from None
        return CollectionRequest(
            site=self.site,
            endpoint=self.endpoint,
            tag_or_group=self.tag,
            window=window,
            page_size=self.page_size,
            max_pages=self.max_pages,
            api_filter=self.api_filter,
            overlap_seconds=self.overlap_seconds,
            backfill=self.backfill,
        )


ENV_FIELDS = {
    "STACKEXCHANGE_SITE": "site",
    "STACKEXCHANGE_TAG": "tag",
    "STACKEXCHANGE_USER_AGENT": "user_agent",
    "STACKEXCHANGE_OUTPUT": "output",
}
RETRY_FIELDS = {"max_attempts", "initial_backoff_seconds", "max_backoff_seconds", "jitter"}


def load_config(
    path: Path,
    environ: Mapping[str, str],
    overrides: Mapping[str, Any],
) -> CollectionConfig:
    try:
        with path.open(encoding="utf-8-sig") as stream:
            loaded = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError("configuration file is missing, unreadable or invalid YAML") from None
    if not isinstance(loaded, dict):
        raise ValueError("configuration must be a YAML mapping")
    allowed = {field.name for field in fields(CollectionConfig)}
    if set(loaded) - allowed - {"retry", "tags"}:
        raise ValueError("unknown configuration key (secrets are forbidden in YAML)")
    retry = loaded.pop("retry", {})
    if not isinstance(retry, dict) or set(retry) - RETRY_FIELDS:
        raise ValueError("unknown or invalid retry configuration")
    if set(retry) & set(loaded):
        raise ValueError("retry settings cannot appear both nested and at top level")
    tags = loaded.pop("tags", None)
    if tags is not None:
        if not isinstance(tags, list) or not tags:
            raise ValueError("tags must be a nonempty list of individual tags")
        for tag in tags:
            if not isinstance(tag, str):
                raise ValueError("each tag must be a string")
            # Reuse domain validation, without creating any network client.
            CollectionRequest(
                "stackoverflow",
                CollectionConfig().endpoint,
                tag,
                CollectionWindow(
                    datetime.fromisoformat("2000-01-01T00:00:00+00:00"),
                    datetime.fromisoformat("2000-01-02T00:00:00+00:00"),
                ),
            )
        if len(tags) == 1 and "tag" not in loaded:
            loaded["tag"] = tags[0]
    values = asdict(CollectionConfig())
    values.update(loaded)
    values.update(retry)
    values.update({field: environ[name] for name, field in ENV_FIELDS.items() if name in environ})
    if set(overrides) - allowed:
        raise ValueError("unknown CLI configuration field")
    values.update({key: value for key, value in overrides.items() if value is not None})
    config = CollectionConfig(**values)
    config.request()
    return config
