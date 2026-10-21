"""Private staging, confirmed shards, and same-volume hard-link promotion."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stackoverflow_vitality.data_dump.errors import DumpAbort
from stackoverflow_vitality.storage.files import ImmutableConflict, IntegrityError, canonical


@dataclass(frozen=True)
class ShardRecord:
    name: str
    sha256: str
    records: int
    size_bytes: int
    first_post_id: int
    last_post_id: int
    path: Path

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "records": self.records,
            "bytes": self.size_bytes,
            "first_post_id": self.first_post_id,
            "last_post_id": self.last_post_id,
        }


class ShardWriter:
    def __init__(
        self, directory: Path, max_records: int, max_bytes: int, max_output_bytes: int
    ) -> None:
        self.directory = directory
        self.max_records = max_records
        self.max_bytes = max_bytes
        self.max_output_bytes = max_output_bytes
        self.output_bytes = 0
        self.shards: list[ShardRecord] = []
        self._lines: list[bytes] = []
        self._pending = 0
        self._first: int | None = None
        self._last: int | None = None

    def add(self, record: dict[str, Any], post_id: int) -> None:
        line = canonical(record) + b"\n"
        if len(line) > self.max_bytes or self.output_bytes + len(line) > self.max_output_bytes:
            raise DumpAbort("DD-021")
        if self._lines and (
            len(self._lines) >= self.max_records or self._pending + len(line) > self.max_bytes
        ):
            self.flush()
        self._lines.append(line)
        self._pending += len(line)
        self.output_bytes += len(line)
        if self._first is None:
            self._first = post_id
        self._last = post_id

    def close(self) -> tuple[ShardRecord, ...]:
        self.flush()
        return tuple(self.shards)

    def flush(self) -> None:
        if not self._lines:
            return
        if self._first is None or self._last is None:
            raise IntegrityError("shard is missing sequence endpoints")
        payload = b"".join(self._lines)
        name = f"shard={len(self.shards) + 1:06d}.jsonl"
        path = self.directory / name
        write_private(path, payload)
        if path.read_bytes() != payload:
            raise IntegrityError("staged shard failed confirmation")
        self.shards.append(
            ShardRecord(
                name=name,
                sha256=hashlib.sha256(payload).hexdigest(),
                records=len(self._lines),
                size_bytes=len(payload),
                first_post_id=self._first,
                last_post_id=self._last,
                path=path,
            )
        )
        self._lines = []
        self._pending = 0
        self._first = None
        self._last = None


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    confirmed = path.read_bytes()
    if confirmed != payload:
        raise IntegrityError("staged bytes differ from the write")


def link_confirmed(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = source.read_bytes()
    if destination.exists():
        if destination.read_bytes() != payload:
            raise ImmutableConflict("existing generation object differs")
        return
    try:
        os.link(source, destination)
    except OSError as error:
        raise ImmutableConflict("hard link failed; the generation was not copied over") from error
    if destination.read_bytes() != payload:
        raise IntegrityError("linked generation object differs")


class QualityLog:
    """Append-only quarantine. One line is buffered, then the record is released."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "items.jsonl"
        self.count = 0

    def add(self, item: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = canonical(item) + b"\n"
        with self.path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        self.count += 1

    def finish(self) -> Path | None:
        if self.count == 0:
            return None
        return self.path


def write_jsonl(directory: Path, name: str, rows: list[dict[str, Any]]) -> Path | None:
    if not rows:
        return None
    payload = b"".join(canonical(row) + b"\n" for row in rows)
    path = directory / name
    write_private(path, payload)
    if path.read_bytes() != payload:
        raise IntegrityError("staged quarantine failed confirmation")
    return path
