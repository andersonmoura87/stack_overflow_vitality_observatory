"""Replaceable boundaries for HTTP and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from stackoverflow_vitality.domain.models import (
    BronzePage,
    ExecutionManifest,
    QuestionSnapshot,
    Watermark,
)


@dataclass(frozen=True)
class ApiResponse:
    items: list[dict[str, Any]]
    has_more: bool
    quota_max: int | None = None
    quota_remaining: int | None = None
    backoff_seconds: int | None = None
    attempts: int = 1
    duration_ms: int = 0
    payload: dict[str, Any] | None = None


class HttpClient(Protocol):
    def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse: ...


class QuestionSnapshotStore(Protocol):
    def write_snapshot(self, snapshot: QuestionSnapshot) -> None: ...

    def validate_write(self, snapshot: QuestionSnapshot) -> bool: ...


class BronzePageStore(Protocol):
    def write_page(self, page: BronzePage) -> None: ...

    def confirm_page(self, page: BronzePage) -> bool: ...

    def write_manifest(self, manifest: ExecutionManifest) -> None: ...

    def confirm_manifest(self, manifest: ExecutionManifest) -> bool: ...


class WatermarkStore(Protocol):
    def read(
        self,
        source: str,
        tag_or_group: str,
        *,
        site: str = "stackoverflow",
        endpoint: str = "https://api.stackexchange.com/2.3/questions",
    ) -> Watermark | None: ...

    def update(self, expected_previous: Watermark | None, new: Watermark) -> None: ...
