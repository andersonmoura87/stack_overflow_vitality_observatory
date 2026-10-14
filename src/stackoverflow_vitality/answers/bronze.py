"""Immutable local storage for answer Bronze pages and run manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

from stackoverflow_vitality.answers.models import SCHEMA_VERSION, AnswerBronzePage, payload_checksum
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.storage.files import IntegrityError, canonical, publish

_BATCH = re.compile(r"^[0-9a-f]{16}$")


def answers_root(bronze: Path) -> Path:
    return bronze / "answers"


def page_path(bronze: Path, page: AnswerBronzePage) -> Path:
    """Identity is site + run + batch hash + page, not the question ids."""
    short = page.batch_id[:16]
    if not _BATCH.fullmatch(short):
        raise ValueError("batch directory must be a short hex digest")
    path = (
        answers_root(bronze)
        / f"site={page.site}"
        / f"run={page.run_id}"
        / f"b={short}"
        / f"page={page.page:04d}.json"
    )
    _contain(bronze, path)
    return path


def manifest_path(bronze: Path, run_id: UUID) -> Path:
    path = answers_root(bronze) / "manifests" / f"{run_id}.json"
    _contain(bronze, path)
    return path


def _contain(root: Path, path: Path) -> None:
    path.resolve().relative_to(root.resolve())


class LocalAnswerBronzeStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write_page(self, page: AnswerBronzePage) -> None:
        if page.checksum != payload_checksum(page.payload):
            raise IntegrityError("answer page checksum does not match the payload")
        publish(page_path(self.root, page), canonical(to_record(page)), immutable=True)

    def confirm_page(self, page: AnswerBronzePage) -> bool:
        path = page_path(self.root, page)
        try:
            loaded = json.loads(path.read_bytes())
        except (OSError, ValueError):
            return False
        return (
            isinstance(loaded, dict)
            and loaded.get("schema_version") == SCHEMA_VERSION
            and loaded.get("checksum") == payload_checksum(page.payload)
            and loaded.get("batch_id") == page.batch_id
            and loaded.get("run_id") == str(page.run_id)
            and loaded.get("question_ids") == list(page.question_ids)
        )

    def write_manifest(self, run_id: UUID, record: dict[str, object]) -> None:
        publish(manifest_path(self.root, run_id), canonical(record), immutable=True)

    def read_manifest(self, run_id: UUID) -> dict[str, object] | None:
        path = manifest_path(self.root, run_id)
        try:
            loaded = json.loads(path.read_bytes())
        except (OSError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) else None
