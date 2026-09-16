"""Local filesystem primitives; hard-link publication never replaces an object."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ImmutableConflict(RuntimeError):
    """An existing object differs from the requested immutable bytes."""


class IntegrityError(RuntimeError):
    """An object does not match its contract or expected bytes."""


class LocalLockConflict(RuntimeError):
    """Another local writer holds the lock or crash recovery is required."""


def canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def matches(path: Path, expected: bytes) -> bool:
    try:
        actual = path.read_bytes()
        return (
            isinstance(json.loads(actual), dict)
            and len(actual) == len(expected)
            and hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest()
        )
    except (OSError, ValueError):
        return False


def publish(path: Path, content: bytes, *, immutable: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if not matches(path, content):
            raise ImmutableConflict("existing immutable object differs")
        return
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if immutable:
            try:
                os.link(name, path)
            except FileExistsError:
                if not matches(path, content):
                    raise ImmutableConflict("concurrent immutable publication differs") from None
        else:
            # Only mutable state, while holding its local lock.
            os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Fail-fast local mkdir lock; stale locks require explicit operator recovery."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError:
        raise LocalLockConflict(
            "local writer lock exists; inspect active writer or recovery"
        ) from None
    try:
        yield
    finally:
        path.rmdir()
