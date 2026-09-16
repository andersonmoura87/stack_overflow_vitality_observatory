"""Immutable local Bronze and locked compare-and-set operational watermarks."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from stackoverflow_vitality.domain.models import (
    BronzePage,
    CollectionStatus,
    CollectionWindow,
    ExecutionManifest,
    RunContext,
    Watermark,
)
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.storage.files import (
    IntegrityError,
    canonical,
    exclusive_lock,
    matches,
    publish,
)

DEFAULT_ENDPOINT = "https://api.stackexchange.com/2.3/questions"


def run_directory(root: Path, context: RunContext) -> Path:
    return (
        root
        / "stackexchange"
        / "questions"
        / f"site={quote(context.site, safe='')}"
        / f"tag={quote(context.tag, safe='')}"
        / f"window_date={context.window.start:%Y-%m-%d}"
        / f"run_id={context.run_id}"
    )


class LocalBronzeStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def page_path(self, page: BronzePage) -> Path:
        context = RunContext(page.run_id, page.site, page.tag, page.window)
        return run_directory(self.root, context) / f"page={page.page:04d}.json"

    def manifest_path(self, manifest: ExecutionManifest) -> Path:
        context = RunContext(
            manifest.run_id,
            manifest.site,
            manifest.tag,
            CollectionWindow(manifest.window_start, manifest.window_end),
        )
        name = (
            "prepared.json"
            if manifest.status == CollectionStatus.PAGES_PERSISTED
            else "manifest.json"
        )
        return run_directory(self.root, context) / name

    def write_page(self, page: BronzePage) -> None:
        if page.checksum != BronzePage.payload_checksum(page.payload):
            raise IntegrityError("payload checksum mismatch")
        publish(self.page_path(page), canonical(to_record(page)))

    def confirm_page(self, page: BronzePage) -> bool:
        return page.checksum == BronzePage.payload_checksum(page.payload) and matches(
            self.page_path(page), canonical(to_record(page))
        )

    def write_manifest(self, manifest: ExecutionManifest) -> None:
        publish(self.manifest_path(manifest), canonical(to_record(manifest)))

    def confirm_manifest(self, manifest: ExecutionManifest) -> bool:
        return matches(self.manifest_path(manifest), canonical(to_record(manifest)))


class WatermarkConflict(RuntimeError):
    """Expected previous state differs or an update would move backwards."""


def watermark_key(source: str, tag: str, site: str, endpoint: str) -> str:
    return json.dumps([source, site, endpoint, tag], separators=(",", ":"))


def validate_update(current: Watermark | None, expected: Watermark | None, new: Watermark) -> None:
    if current != expected:
        raise WatermarkConflict("expected previous watermark differs")
    if current is not None and new.value < current.value:
        raise WatermarkConflict("watermark cannot move backwards")


class LocalWatermarkStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _records(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            records = json.loads(self.path.read_bytes())
            if not isinstance(records, dict):
                raise ValueError
            for key, record in records.items():
                mark = self._decode(record)
                if key != watermark_key(mark.source, mark.tag_or_group, mark.site, mark.endpoint):
                    raise ValueError
            return records
        except (ValueError, TypeError, KeyError, AttributeError):
            raise IntegrityError("corrupt watermark JSON or unsupported legacy schema") from None

    @staticmethod
    def _decode(record: dict[str, Any]) -> Watermark:
        if not isinstance(record, dict) or set(record) != {
            "source",
            "site",
            "endpoint",
            "tag_or_group",
            "value",
            "updated_at",
        }:
            raise ValueError("invalid watermark record")
        if any(not isinstance(value, str) for value in record.values()):
            raise ValueError("invalid watermark field")
        return Watermark(
            source=record["source"],
            site=record["site"],
            endpoint=record["endpoint"],
            tag_or_group=record["tag_or_group"],
            value=datetime.fromisoformat(record["value"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00")),
        )

    def read(
        self,
        source: str,
        tag_or_group: str,
        *,
        site: str = "stackoverflow",
        endpoint: str = DEFAULT_ENDPOINT,
    ) -> Watermark | None:
        record = self._records().get(watermark_key(source, tag_or_group, site, endpoint))
        return None if record is None else self._decode(record)

    def update(self, expected_previous: Watermark | None, new: Watermark) -> None:
        with exclusive_lock(self.path.with_name(self.path.name + ".lock")):
            records = self._records()
            key = watermark_key(new.source, new.tag_or_group, new.site, new.endpoint)
            current = self._decode(records[key]) if key in records else None
            validate_update(current, expected_previous, new)
            records[key] = to_record(new)
            publish(self.path, canonical(records), immutable=False)
