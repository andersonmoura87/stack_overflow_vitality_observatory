"""CAS checkpoint for answer batches. It is not a watermark per answer_id."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from stackoverflow_vitality.answers.models import STRATEGY, AnswerCheckpoint, utc
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.storage.files import exclusive_lock, publish


class CheckpointConflict(RuntimeError):
    """The stored cursor does not match the expected predecessor."""


def checkpoint_path(bronze: Path) -> Path:
    return bronze / "answers" / "checkpoint.json"


class LocalAnswerCheckpoint:
    def __init__(self, bronze: str | Path) -> None:
        self.path = checkpoint_path(Path(bronze))

    def read(self, key: str) -> AnswerCheckpoint | None:
        records = self._records()
        raw = records.get(key)
        return None if raw is None else _decode(raw)

    def update(self, expected: AnswerCheckpoint | None, new: AnswerCheckpoint) -> None:
        with exclusive_lock(self.path.with_name(self.path.name + ".lock")):
            records = self._records()
            current = _decode(records[new.key]) if new.key in records else None
            if (current is None) != (expected is None):
                raise CheckpointConflict("answer checkpoint predecessor mismatch")
            if current is not None and expected is not None:
                if (
                    current.completed_batch_ids != expected.completed_batch_ids
                    or current.completed_question_ids != expected.completed_question_ids
                ):
                    raise CheckpointConflict("answer checkpoint predecessor mismatch")
            previous_batches = set() if expected is None else set(expected.completed_batch_ids)
            previous_ids = set() if expected is None else set(expected.completed_question_ids)
            if not previous_batches <= set(new.completed_batch_ids):
                raise CheckpointConflict("answer checkpoint must be monotonic")
            if not previous_ids <= set(new.completed_question_ids):
                raise CheckpointConflict("answer checkpoint must be monotonic")
            if new.strategy != STRATEGY:
                raise CheckpointConflict("answer checkpoint strategy must be question_id_batches")
            records[new.key] = to_record(new)
            publish(self.path, _encode(records), immutable=False)

    def _records(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            loaded = json.loads(self.path.read_bytes())
        except (OSError, ValueError):
            raise ValueError("answer checkpoint is unreadable") from None
        if not isinstance(loaded, dict):
            raise ValueError("answer checkpoint must be an object")
        records: dict[str, dict[str, object]] = {}
        for key, value in loaded.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise ValueError("answer checkpoint record is invalid")
            records[key] = value
        return records


def _encode(records: dict[str, dict[str, object]]) -> bytes:
    from stackoverflow_vitality.storage.files import canonical

    return canonical(records)


def _decode(record: dict[str, object]) -> AnswerCheckpoint:
    required = {
        "source",
        "site",
        "endpoint",
        "strategy",
        "completed_batch_ids",
        "completed_question_ids",
        "updated_at",
    }
    if set(record) != required:
        raise ValueError("invalid answer checkpoint record")
    batches = record["completed_batch_ids"]
    questions = record["completed_question_ids"]
    updated = record["updated_at"]
    if (
        not isinstance(batches, list)
        or not isinstance(questions, list)
        or not isinstance(updated, str)
        or any(not isinstance(item, str) for item in batches)
        or any(type(item) is not int or item < 1 for item in questions)
    ):
        raise ValueError("invalid answer checkpoint record")
    source = record["source"]
    site = record["site"]
    endpoint = record["endpoint"]
    strategy = record["strategy"]
    if not all(isinstance(item, str) for item in (source, site, endpoint, strategy)):
        raise ValueError("invalid answer checkpoint record")
    assert isinstance(source, str)
    assert isinstance(site, str)
    assert isinstance(endpoint, str)
    assert isinstance(strategy, str)
    return AnswerCheckpoint(
        source=source,
        site=site,
        endpoint=endpoint,
        strategy=strategy,
        completed_batch_ids=tuple(str(item) for item in batches),
        completed_question_ids=tuple(int(item) for item in questions),
        updated_at=utc(datetime.fromisoformat(updated.replace("Z", "+00:00")), "updated_at"),
    )
