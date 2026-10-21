"""Offline Data Dump settings. Precedence is CLI, then environment, then YAML."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from stackoverflow_vitality.data_dump.dates import normalize_cutoff, parse_dump_instant

ENV_FIELDS = {
    "STACKEXCHANGE_DUMP_SITE": "site",
    "STACKEXCHANGE_DUMP_PUBLICATION_ID": "publication_id",
    "STACKEXCHANGE_DUMP_CUTOFF": "dump_cutoff",
    "STACKEXCHANGE_DUMP_BRONZE": "bronze_output",
    "STACKEXCHANGE_DUMP_QUARANTINE": "quarantine_output",
    "STACKEXCHANGE_DUMP_MAX_FILE_BYTES": "max_file_bytes",
    "STACKEXCHANGE_DUMP_MAX_RECORDS": "max_records",
    "STACKEXCHANGE_DUMP_MAX_ATTRIBUTE_BYTES": "max_attribute_bytes",
    "STACKEXCHANGE_DUMP_MAX_BODY_CHARS": "max_body_chars",
    "STACKEXCHANGE_DUMP_MAX_OUTPUT_BYTES": "max_output_bytes",
    "STACKEXCHANGE_DUMP_MAX_SHARD_RECORDS": "max_shard_records",
    "STACKEXCHANGE_DUMP_MAX_SHARD_BYTES": "max_shard_bytes",
    "STACKEXCHANGE_DUMP_MIN_FREE_BYTES": "min_free_bytes",
    "STACKEXCHANGE_DUMP_PROGRESS_EVERY": "progress_every",
    "STACKEXCHANGE_DUMP_REVISION": "project_revision",
    "STACKEXCHANGE_DUMP_INPUT_ROOT": "input_root",
    "STACKEXCHANGE_DUMP_RECORD_RELATIVE_PATH": "record_relative_path",
    "STACKEXCHANGE_DUMP_MAX_RSS_BYTES": "max_rss_bytes",
}
INT_FIELDS = {
    "max_file_bytes",
    "max_records",
    "max_attribute_bytes",
    "max_body_chars",
    "max_output_bytes",
    "max_shard_records",
    "max_shard_bytes",
    "min_free_bytes",
    "progress_every",
    "max_rss_bytes",
}
SECRET_KEYS = {"key", "api_key", "access_token", "password", "secret"}
_SITE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_PUBLICATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GIBIBYTE = 1024**3


@dataclass(frozen=True)
class DumpIngestConfig:
    site: str = "stackoverflow"
    publication_id: str = ""
    dump_cutoff: str | None = None
    bronze_output: str = "data/bronze"
    quarantine_output: str = "data/quarantine"
    max_file_bytes: int = 8 * GIBIBYTE
    max_records: int = 100_000
    max_attribute_bytes: int = 1024**2
    max_body_chars: int = 500_000
    max_output_bytes: int = 2 * GIBIBYTE
    max_shard_records: int = 5_000
    max_shard_bytes: int = 64 * 1024**2
    min_free_bytes: int = GIBIBYTE
    progress_every: int = 100_000
    record_relative_path: bool = False
    input_root: str = ""
    project_revision: str | None = None
    max_rss_bytes: int | None = None

    def validate(self) -> None:
        if not _SITE.fullmatch(self.site) or ".." in self.site:
            raise ValueError("site must be a short label without separators")
        if not _PUBLICATION.fullmatch(self.publication_id):
            raise ValueError("publication_id must be a label, not a date or path")
        if self.dump_cutoff is not None and parse_dump_instant(self.dump_cutoff[:-1]) is None:
            raise ValueError("dump_cutoff must be a UTC timestamp")
        for name in INT_FIELDS - {"max_rss_bytes"}:
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"invalid configuration field: {name}")
        if self.max_rss_bytes is not None and (
            type(self.max_rss_bytes) is not int or self.max_rss_bytes < 1
        ):
            raise ValueError("invalid configuration field: max_rss_bytes")
        if type(self.record_relative_path) is not bool:
            raise ValueError("invalid configuration field: record_relative_path")
        if self.record_relative_path and not self.input_root:
            raise ValueError("input_root is required when recording a relative path")
        if self.project_revision is not None and (
            not isinstance(self.project_revision, str)
            or not self.project_revision.strip()
            or any(ord(char) < 32 for char in self.project_revision)
        ):
            raise ValueError("project_revision must be printable text or absent")
        for directory in (self.bronze_output, self.quarantine_output, self.input_root):
            if not directory:
                continue
            if "\x00" in directory:
                raise ValueError("invalid output directory")
            path = Path(directory)
            if any(item.exists() and not item.is_dir() for item in (path, *path.parents)):
                raise ValueError("output must be a directory, with directory ancestors")

    @property
    def cutoff_status(self) -> str:
        return "unknown" if self.dump_cutoff is None else "operator_declared"


def load_dump_config(
    path: Path,
    environ: Mapping[str, str],
    overrides: Mapping[str, Any],
) -> DumpIngestConfig:
    try:
        with path.open(encoding="utf-8-sig") as stream:
            loaded = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError("configuration file is missing, unreadable or invalid YAML") from None
    if not isinstance(loaded, dict):
        raise ValueError("configuration must be a YAML mapping")
    if SECRET_KEYS & set(loaded):
        raise ValueError("unknown configuration key (secrets are forbidden in YAML)")
    if loaded.get("mode", "restricted") != "restricted":
        raise ValueError("mode must be restricted")
    loaded.pop("mode", None)
    allowed = {field.name for field in fields(DumpIngestConfig)}
    if set(loaded) - allowed:
        raise ValueError("unknown configuration key (secrets are forbidden in YAML)")
    values = asdict(DumpIngestConfig())
    values.update(loaded)
    values.update({field: environ[name] for name, field in ENV_FIELDS.items() if name in environ})
    if set(overrides) - allowed:
        raise ValueError("unknown CLI configuration field")
    values.update({key: value for key, value in overrides.items() if value is not None})
    _coerce(values)
    values["dump_cutoff"] = normalize_cutoff(values["dump_cutoff"])
    config = DumpIngestConfig(**values)
    config.validate()
    return config


def _coerce(values: dict[str, Any]) -> None:
    for name in INT_FIELDS:
        value = values[name]
        if value is None and name == "max_rss_bytes":
            continue
        if isinstance(value, str) and value.isdigit():
            values[name] = int(value)
    flag = values["record_relative_path"]
    if isinstance(flag, str):
        lowered = flag.lower()
        if lowered not in {"true", "false"}:
            raise ValueError("invalid configuration field: record_relative_path")
        values["record_relative_path"] = lowered == "true"
    revision = values["project_revision"]
    if revision == "":
        values["project_revision"] = None
