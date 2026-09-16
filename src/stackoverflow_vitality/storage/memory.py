"""Deterministic in-memory stores used by tests and local experiments."""

from __future__ import annotations

from stackoverflow_vitality.domain.models import (
    BronzePage,
    ExecutionManifest,
    QuestionSnapshot,
    Watermark,
)
from stackoverflow_vitality.storage.bronze import validate_update, watermark_key
from stackoverflow_vitality.storage.files import ImmutableConflict


class MemorySnapshotStore:
    def __init__(self) -> None:
        self.records: dict[int, QuestionSnapshot] = {}

    def write_snapshot(self, snapshot: QuestionSnapshot) -> None:
        self.records.setdefault(snapshot.question_id, snapshot)

    def validate_write(self, snapshot: QuestionSnapshot) -> bool:
        return self.records.get(snapshot.question_id) == snapshot


class MemoryWatermarkStore:
    def __init__(self) -> None:
        self.records: dict[str, Watermark] = {}

    def read(
        self,
        source: str,
        tag_or_group: str,
        *,
        site: str = "stackoverflow",
        endpoint: str = "https://api.stackexchange.com/2.3/questions",
    ) -> Watermark | None:
        return self.records.get(watermark_key(source, tag_or_group, site, endpoint))

    def update(self, expected_previous: Watermark | None, new: Watermark) -> None:
        key = watermark_key(new.source, new.tag_or_group, new.site, new.endpoint)
        validate_update(self.records.get(key), expected_previous, new)
        self.records[key] = new


class MemoryBronzeStore:
    def __init__(self) -> None:
        self.pages: list[BronzePage] = []
        self.manifests: list[ExecutionManifest] = []
        self.prepared: list[ExecutionManifest] = []

    def write_page(self, page: BronzePage) -> None:
        for existing in self.pages:
            if (existing.run_id, existing.page) == (page.run_id, page.page):
                if existing != page:
                    raise ImmutableConflict("page differs")
                return
        self.pages.append(page)

    def confirm_page(self, page: BronzePage) -> bool:
        return page in self.pages

    def write_manifest(self, manifest: ExecutionManifest) -> None:
        target = self.prepared if manifest.status == "pages_persisted" else self.manifests
        for existing in target:
            if existing.run_id == manifest.run_id:
                if existing != manifest:
                    raise ImmutableConflict("manifest differs")
                return
        target.append(manifest)

    def confirm_manifest(self, manifest: ExecutionManifest) -> bool:
        return manifest in self.manifests or manifest in self.prepared
