"""Immutable domain models for collection and provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must have timezone information")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class CollectionWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _utc(self.start, "start")
        end = _utc(self.end, "end")
        if start > end:
            raise ValueError("window start must not be after window end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True)
class CollectionRequest:
    site: str
    endpoint: str
    tag_or_group: str
    window: CollectionWindow
    page_size: int = 100
    max_pages: int = 25
    api_filter: str = "default"
    start_page: int = 1
    overlap_seconds: int = 86400
    backfill: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.site):
            raise ValueError("site must be a nonempty site identifier")
        endpoint = urlsplit(self.endpoint)
        if (
            endpoint.scheme != "https"
            or not endpoint.netloc
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("endpoint must use HTTPS without credentials, query or fragment")
        if not self.tag_or_group.strip():
            raise ValueError("tag_or_group is required")
        if ";" in self.tag_or_group:
            raise ValueError(
                "tag_or_group must be one tag or an explicit group, not an AND tag list"
            )
        if not re.fullmatch(r"[a-z0-9.+#-]+", self.tag_or_group):
            raise ValueError("tag must be one lowercase tag")
        for name in ("page_size", "max_pages", "start_page", "overlap_seconds"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if type(self.backfill) is not bool:
            raise ValueError("backfill must be boolean")
        if not 1 <= self.page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if self.max_pages < 1:
            raise ValueError("max_pages must be positive")
        if self.start_page < 1:
            raise ValueError("start_page must be positive")
        if self.overlap_seconds < 0:
            raise ValueError("overlap_seconds cannot be negative")
        if not self.backfill and self.start_page != 1:
            raise ValueError("operational collection must start at page 1")


@dataclass(frozen=True)
class SourceMetadata:
    source: str
    endpoint: str
    site: str
    tag_or_group: str
    window: CollectionWindow
    page: int
    api_filter: str
    collected_at: datetime

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be positive")
        object.__setattr__(self, "collected_at", _utc(self.collected_at, "collected_at"))


@dataclass(frozen=True)
class QuestionSnapshot:
    question_id: int
    payload: Mapping[str, Any]
    metadata: SourceMetadata

    def __post_init__(self) -> None:
        if self.question_id < 1:
            raise ValueError("question_id must be positive")


@dataclass(frozen=True)
class IngestionRun:
    run_id: UUID
    request: CollectionRequest
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    items_received: int = 0
    pages_received: int = 0
    truncated: bool = False
    quota_remaining: int | None = None
    errors: tuple[str, ...] = ()
    watermark_before: datetime | None = None
    watermark_after: datetime | None = None
    failure_stage: str | None = None

    def __post_init__(self) -> None:
        started = _utc(self.started_at, "started_at")
        object.__setattr__(self, "started_at", started)
        if self.finished_at is not None:
            finished = _utc(self.finished_at, "finished_at")
            if finished < started:
                raise ValueError("finished_at must not be before started_at")
            object.__setattr__(self, "finished_at", finished)
        if self.items_received < 0 or self.pages_received < 0:
            raise ValueError("run counters cannot be negative")
        if self.watermark_before is not None:
            object.__setattr__(
                self, "watermark_before", _utc(self.watermark_before, "watermark_before")
            )
        if self.watermark_after is not None:
            object.__setattr__(
                self, "watermark_after", _utc(self.watermark_after, "watermark_after")
            )


class CollectionStatus(StrEnum):
    PAGES_PERSISTED = "pages_persisted"
    COMPLETED = "completed"
    FAILED = "failed"
    TRUNCATED = "truncated"
    EMPTY = "empty"


@dataclass(frozen=True)
class BronzePage:
    run_id: UUID
    source: str
    endpoint: str
    site: str
    tag: str
    window: CollectionWindow
    page: int
    recovered_at: datetime
    payload: Mapping[str, Any]
    request_params: Mapping[str, str | int]
    quota_max: int | None
    quota_remaining: int | None
    backoff_seconds: int | None
    has_more: bool
    collector_version: str
    schema_version: str
    checksum: str
    replayed_from_run_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be positive")
        if not self.endpoint.startswith("https://"):
            raise ValueError("endpoint must use HTTPS")
        object.__setattr__(self, "recovered_at", _utc(self.recovered_at, "recovered_at"))

    @staticmethod
    def payload_checksum(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExecutionManifest:
    run_id: UUID
    source: str
    endpoint: str
    site: str
    tag: str
    window_start: datetime
    window_end: datetime
    started_at: datetime
    finished_at: datetime
    status: CollectionStatus
    pages_requested: int
    pages_persisted: int
    items_received: int
    quota_remaining: int | None
    truncated: bool
    watermark_before: datetime | None
    watermark_after: datetime | None
    collector_version: str
    errors: tuple[str, ...] = ()
    failure_stage: str | None = None
    backfill: bool = False

    def __post_init__(self) -> None:
        for field_name in ("window_start", "window_end", "started_at", "finished_at"):
            object.__setattr__(self, field_name, _utc(getattr(self, field_name), field_name))
        if self.watermark_before is not None:
            object.__setattr__(
                self, "watermark_before", _utc(self.watermark_before, "watermark_before")
            )
        if self.watermark_after is not None:
            object.__setattr__(
                self, "watermark_after", _utc(self.watermark_after, "watermark_after")
            )
        if self.pages_requested < 0 or self.pages_persisted < 0 or self.items_received < 0:
            raise ValueError("manifest counters cannot be negative")
        if self.window_start > self.window_end or self.started_at > self.finished_at:
            raise ValueError("manifest intervals must be ordered")
        if self.pages_persisted > self.pages_requested:
            raise ValueError("persisted pages cannot exceed requested pages")
        if self.status in {CollectionStatus.COMPLETED, CollectionStatus.EMPTY}:
            if self.truncated or self.errors:
                raise ValueError("successful manifest cannot contain truncation or errors")
            if not self.backfill and self.watermark_after != self.window_end:
                raise ValueError("successful operational manifest requires committed watermark")
        if self.backfill and self.watermark_after is not None:
            raise ValueError("backfill must not update operational watermark")


@dataclass(frozen=True)
class Watermark:
    source: str
    tag_or_group: str
    value: datetime
    updated_at: datetime
    site: str = "stackoverflow"
    endpoint: str = "https://api.stackexchange.com/2.3/questions"

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _utc(self.value, "value"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))


@dataclass(frozen=True)
class RunContext:
    run_id: UUID
    site: str
    tag: str
    window: CollectionWindow
