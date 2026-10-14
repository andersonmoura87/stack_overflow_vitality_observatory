"""Answer collection settings. Question YAML remains a separate document."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

ENV_FIELDS = {
    "STACKEXCHANGE_ANSWERS_SITE": "site",
    "STACKEXCHANGE_ANSWERS_ENDPOINT": "endpoint",
    "STACKEXCHANGE_ANSWERS_USER_AGENT": "user_agent",
    "STACKEXCHANGE_ANSWERS_BRONZE": "bronze_output",
    "STACKEXCHANGE_ANSWERS_SILVER": "silver_output",
}
RETRY_FIELDS = {"max_attempts", "initial_backoff_seconds", "max_backoff_seconds", "jitter"}
SECRET_KEYS = {"key", "api_key", "access_token", "password", "secret"}


@dataclass(frozen=True)
class AnswerCollectionConfig:
    site: str = "stackoverflow"
    endpoint: str = "https://api.stackexchange.com/2.3/questions"
    page_size: int = 100
    max_pages: int = 10
    max_batch_size: int = 20
    max_url_length: int = 2000
    query_allowance: int = 256
    concurrency: int = 1
    api_filter: str = "withbody"
    user_agent: str = "stackoverflow-vitality-observatory/0.2"
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_attempts: int = 4
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    jitter: float = 1.0
    bronze_output: str = "data/bronze"
    silver_output: str = "data/silver"
    mode: str = "incremental"

    def validate(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            default = getattr(AnswerCollectionConfig(), field.name)
            if isinstance(default, int):
                valid = type(value) is int
            elif isinstance(default, float):
                valid = type(value) in (int, float) and math.isfinite(float(value))
            else:
                valid = isinstance(value, str) and bool(value.strip())
            if not valid:
                raise ValueError(f"invalid configuration field: {field.name}")
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.username
            or not parsed.path.rstrip("/").endswith("/questions")
        ):
            raise ValueError("endpoint must be the HTTPS /questions collection URL")
        if not 1 <= self.page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if self.max_pages < 1 or self.max_batch_size < 1:
            raise ValueError("page and batch limits must be positive")
        if self.max_url_length < 32 or self.query_allowance < 0:
            raise ValueError("URL budget is invalid")
        if self.concurrency != 1:
            raise ValueError("concurrency must stay at 1 until a later phase")
        if self.mode not in {"incremental", "refresh"}:
            raise ValueError("mode must be incremental or refresh")
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
        for directory in (self.bronze_output, self.silver_output):
            if "\x00" in directory:
                raise ValueError("invalid output directory")
            path = Path(directory)
            if any(item.exists() and not item.is_dir() for item in (path, *path.parents)):
                raise ValueError("output must be a directory, with directory ancestors")


def load_answer_config(
    path: Path,
    environ: Mapping[str, str],
    overrides: Mapping[str, Any],
) -> AnswerCollectionConfig:
    try:
        with path.open(encoding="utf-8-sig") as stream:
            loaded = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError("configuration file is missing, unreadable or invalid YAML") from None
    if not isinstance(loaded, dict):
        raise ValueError("configuration must be a YAML mapping")
    if SECRET_KEYS & set(loaded):
        raise ValueError("unknown configuration key (secrets are forbidden in YAML)")
    allowed = {field.name for field in fields(AnswerCollectionConfig)}
    if set(loaded) - allowed - {"retry"}:
        raise ValueError("unknown configuration key (secrets are forbidden in YAML)")
    retry = loaded.pop("retry", {})
    if not isinstance(retry, dict) or set(retry) - RETRY_FIELDS:
        raise ValueError("unknown or invalid retry configuration")
    if set(retry) & set(loaded):
        raise ValueError("retry settings cannot appear both nested and at top level")
    values = asdict(AnswerCollectionConfig())
    values.update(loaded)
    values.update(retry)
    values.update({field: environ[name] for name, field in ENV_FIELDS.items() if name in environ})
    if set(overrides) - allowed:
        raise ValueError("unknown CLI configuration field")
    values.update({key: value for key, value in overrides.items() if value is not None})
    config = AnswerCollectionConfig(**values)
    config.validate()
    return config
