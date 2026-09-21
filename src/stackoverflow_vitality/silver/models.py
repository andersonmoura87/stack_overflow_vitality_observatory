"""Typed Silver entities and executable JSON contract."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.storage.files import canonical

SCHEMA_VERSION = "silver_questions.v1"
TRANSFORMER_VERSION = "1.0.0"
CONTENT_FIELDS = (
    "schema_version",
    "site",
    "question_id",
    "title_raw",
    "body_html",
    "tags",
    "creation_at",
    "last_edit_at",
    "owner_user_id",
    "content_license",
    "link",
    "closed_at",
    "closed_reason",
)
DERIVED_FIELDS = ("title_clean", "body_text", "code_blocks")
METRIC_FIELDS = (
    "score",
    "view_count",
    "answer_count",
    "is_answered",
    "accepted_answer_id",
    "last_activity_at",
)
DATE_FIELDS = {
    "creation_at",
    "last_activity_at",
    "last_edit_at",
    "closed_at",
    "collected_at",
    "valid_from",
    "valid_to",
}
INT_FIELDS = {
    "question_id",
    "score",
    "view_count",
    "answer_count",
    "accepted_answer_id",
    "owner_user_id",
    "source_page",
    "source_item_index",
}
ARRAY_FIELDS = {"tags", "code_blocks"}
BOOL_FIELDS = {"is_answered", "is_current"}
NULLABLE = {
    "title_raw",
    "title_clean",
    "body_html",
    "body_text",
    "code_blocks",
    "tags",
    "last_activity_at",
    "last_edit_at",
    "score",
    "view_count",
    "answer_count",
    "is_answered",
    "accepted_answer_id",
    "owner_user_id",
    "content_license",
    "link",
    "closed_at",
    "closed_reason",
    "valid_to",
}


class ContractError(ValueError):
    """Silver data violates its published contract."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(UTC)


def content_hash(record: dict[str, Any]) -> str:
    return digest(canonical({key: record[key] for key in CONTENT_FIELDS}))


@dataclass(frozen=True, kw_only=True)
class SilverQuestion:
    question_id: int
    site: str
    title_raw: str | None
    title_clean: str | None
    body_html: str | None
    body_text: str | None
    code_blocks: tuple[str, ...] | None
    tags: tuple[str, ...] | None
    creation_at: datetime
    last_activity_at: datetime | None
    last_edit_at: datetime | None
    score: int | None
    view_count: int | None
    answer_count: int | None
    is_answered: bool | None
    accepted_answer_id: int | None
    owner_user_id: int | None
    content_license: str | None
    link: str | None
    closed_at: datetime | None
    closed_reason: str | None
    collected_at: datetime
    source_run_id: str
    source_page: int
    source_checksum: str
    source_file_checksum: str
    source_path: str
    source_item_index: int
    record_hash: str
    schema_version: str
    valid_from: datetime
    valid_to: datetime | None
    is_current: bool

    def __post_init__(self) -> None:
        validate_record(to_record(self))

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> SilverQuestion:
        validate_record(record)
        values = dict(record)
        for key in DATE_FIELDS:
            if values[key] is not None:
                values[key] = utc(values[key])
        for key in ARRAY_FIELDS:
            if values[key] is not None:
                values[key] = tuple(values[key])
        return cls(**values)


def validate_record(record: dict[str, Any]) -> None:
    expected = {field.name for field in fields(SilverQuestion)}
    if set(record) != expected:
        raise ContractError("Silver fields differ from schema")
    for key, value in record.items():
        if value is None:
            if key not in NULLABLE:
                raise ContractError(f"required field: {key}")
            continue
        if key in INT_FIELDS:
            valid = type(value) is int
        elif key in BOOL_FIELDS:
            valid = type(value) is bool
        elif key in ARRAY_FIELDS:
            valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
        else:
            valid = isinstance(value, str)
        if not valid:
            raise ContractError(f"incompatible field type: {key}")
        if key in DATE_FIELDS:
            try:
                if not value.endswith("Z"):
                    raise ValueError
                utc(value)
            except (ValueError, TypeError):
                raise ContractError(f"invalid UTC timestamp: {key}") from None
    if record["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported Silver schema")
    for key in ("site", "source_run_id", "source_path"):
        if not record[key]:
            raise ContractError(f"missing lineage or identity: {key}")
    try:
        UUID(record["source_run_id"])
    except ValueError:
        raise ContractError("invalid source run UUID") from None
    for key in ("record_hash", "source_checksum", "source_file_checksum"):
        if not re.fullmatch(r"[0-9a-f]{64}", record[key]):
            raise ContractError(f"invalid checksum: {key}")
    if record["record_hash"] != content_hash(record):
        raise ContractError("content hash mismatch")
    if record["question_id"] <= 0 or record["source_page"] <= 0 or record["source_item_index"] < 0:
        raise ContractError("invalid identity")
    for key in ("view_count", "answer_count"):
        if record[key] is not None and record[key] < 0:
            raise ContractError("negative counter")
    if record["tags"] is not None and record["tags"] != sorted(set(record["tags"])):
        raise ContractError("tags must be sorted and unique")
    if record["is_current"] != (record["valid_to"] is None):
        raise ContractError("current flag disagrees with interval")
    if record["valid_to"] is not None and utc(record["valid_to"]) <= utc(record["valid_from"]):
        raise ContractError("nonpositive validity interval")


def validate_history(records: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for record in records:
        validate_record(record)
        groups.setdefault((record["site"], record["question_id"]), []).append(record)
    for versions in groups.values():
        versions.sort(key=lambda item: item["valid_from"])
        if sum(item["is_current"] for item in versions) != 1 or not versions[-1]["is_current"]:
            raise ContractError("exactly one latest current version is required")
        for before, after in zip(versions, versions[1:], strict=False):
            if before["valid_to"] != after["valid_from"]:
                raise ContractError("history must have contiguous nonoverlapping intervals")


@dataclass(frozen=True)
class InputPage:
    path: str
    file_checksum: str
    envelope: dict[str, Any]
    items: tuple[object, ...]


@dataclass(frozen=True)
class QualityEvent:
    transformation_run_id: str
    source_run_id: str | None
    source_page: int | None
    question_id: int | None
    rule_id: str
    severity: Literal["error", "warning", "informational"]
    reason: str
    field: str
    observed_value: str
    quarantined_at: datetime
    source_path: str
    source_item_index: int


@dataclass(frozen=True)
class TransformationManifest:
    transformation_run_id: str
    dataset_id: str
    commit_sequence: int
    source_run_ids: tuple[str, ...]
    started_at: datetime
    finished_at: datetime
    status: str
    schema_version: str
    input_pages: int
    input_records: int
    accepted_records: int
    quarantined_records: int
    unchanged_records: int
    new_versions: int
    closed_versions: int
    quality_errors: int
    quality_warnings: int
    input_checksums: dict[str, str]
    output_checksums: dict[str, str]
    output_paths: dict[str, str]
    transformer_version: str = TRANSFORMER_VERSION
    failure_stage: str | None = None
    error_type: str | None = None
