"""Bronze and checkpoint records for answer collection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

SCHEMA_VERSION = "bronze_stackexchange_answers_snapshot.v1"
STRATEGY = "question_id_batches"
SOURCE = "stackexchange_api"
COLLECTOR_VERSION = "0.3.0"


def payload_checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must have timezone information")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class AnswerBronzePage:
    run_id: UUID
    source: str
    endpoint: str
    site: str
    strategy: str
    batch_id: str
    batch_index: int
    question_ids: tuple[int, ...]
    question_ids_origin: str
    page: int
    requested_at: datetime
    recovered_at: datetime
    payload: dict[str, Any]
    request_params: dict[str, str | int]
    has_more: bool
    quota_max: int | None
    quota_remaining: int | None
    backoff_seconds: int | None
    http_status: int
    collector_version: str
    user_agent: str
    item_count: int
    truncated: bool
    checksum: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unexpected answer bronze schema")
        if self.strategy != STRATEGY or self.source != SOURCE:
            raise ValueError("unexpected answer collection identity")
        if self.page < 1 or self.batch_index < 0 or self.item_count < 0:
            raise ValueError("answer page counters must be valid")
        if not self.question_ids:
            raise ValueError("answer page requires the requested question ids")
        if self.http_status != 200:
            raise ValueError("only a parsed HTTP 200 page is stored")
        object.__setattr__(self, "requested_at", utc(self.requested_at, "requested_at"))
        object.__setattr__(self, "recovered_at", utc(self.recovered_at, "recovered_at"))
        if "key" in self.request_params or "access_token" in self.request_params:
            raise ValueError("request params must not carry a secret")
        if len(self.checksum) != 64:
            raise ValueError("checksum must be a SHA-256 hex digest")


@dataclass(frozen=True)
class AnswerCheckpoint:
    source: str
    site: str
    endpoint: str
    strategy: str
    completed_batch_ids: tuple[str, ...]
    completed_question_ids: tuple[int, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.strategy != STRATEGY:
            raise ValueError("answer checkpoint strategy must be question_id_batches")
        if self.completed_batch_ids != tuple(sorted(set(self.completed_batch_ids))):
            raise ValueError("completed batches must be unique and sorted")
        if self.completed_question_ids != tuple(sorted(set(self.completed_question_ids))):
            raise ValueError("completed question ids must be unique and sorted")
        object.__setattr__(self, "updated_at", utc(self.updated_at, "updated_at"))

    @property
    def key(self) -> str:
        return f"{self.source}|{self.site}|{self.endpoint}|{self.strategy}"
