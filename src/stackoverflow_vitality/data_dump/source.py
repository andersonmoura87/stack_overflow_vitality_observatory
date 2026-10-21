"""Fail-closed inspection of a local uncompressed Posts.xml."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

SCAN_CHUNK = 1024**2
FORBIDDEN_XML_TOKENS = (
    b"<!doctype",
    b"<!entity",
    b"<!element",
    b"<!attlist",
    b"<!notation",
)
_ROOT = b"<posts"
_REPARSE = 0x400
_INVALID_ATTRIBUTES = 0xFFFFFFFF


def forbidden_overlap(tokens: tuple[bytes, ...] = FORBIDDEN_XML_TOKENS) -> int:
    """Bytes retained between chunks. Derived from the token collection."""
    return max(len(token) for token in tokens) - 1


class DumpSourceError(ValueError):
    """The source is refused before a generation exists."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceFacts:
    name: str
    size: int
    device: int
    inode: int


@dataclass(frozen=True)
class SourceMeasurement:
    facts: SourceFacts
    sha256: str
    size: int


def reject_null(value: str) -> None:
    if "\x00" in value:
        raise DumpSourceError("source_refused")


def sanitized_name(path: Path) -> str:
    name = path.name
    if name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise DumpSourceError("source_refused")
    return name


def relative_source(path: Path, root: str) -> str:
    if ".." in path.parts:
        raise ValueError("relative path must not contain ..")
    root_path = Path(root).resolve()
    try:
        relative = path.resolve().relative_to(root_path)
    except ValueError:
        raise ValueError("source is outside the input root") from None
    text = relative.as_posix()
    if text.startswith("/") or ".." in text.split("/"):
        raise ValueError("relative path must not contain ..")
    return text


def windows_attributes(path: Path) -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    kernel = getattr(ctypes.windll, "kernel32", None)
    if kernel is None:
        raise DumpSourceError("source_refused")
    reader = kernel.GetFileAttributesW
    reader.argtypes = (ctypes.c_wchar_p,)
    reader.restype = ctypes.c_uint32
    return int(reader(str(path)))


def inspect_source(
    path: Path,
    *,
    max_file_bytes: int,
    attributes: Callable[[Path], int | None] | None = None,
    chunk_size: int = SCAN_CHUNK,
) -> SourceFacts:
    stream, facts = open_checked(path, max_file_bytes=max_file_bytes, attributes=attributes)
    try:
        _scan(stream, chunk_size=chunk_size, digest=False)
    finally:
        stream.close()
    return facts


def open_checked(
    path: Path,
    *,
    max_file_bytes: int,
    attributes: Callable[[Path], int | None] | None = None,
) -> tuple[BinaryIO, SourceFacts]:
    reject_null(str(path))
    if not path.exists():
        raise DumpSourceError("source_refused")
    reader = windows_attributes if attributes is None else attributes
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise DumpSourceError("source_refused")
    flags = reader(path)
    if flags is None:
        pass
    elif flags == _INVALID_ATTRIBUTES or flags & _REPARSE:
        raise DumpSourceError("source_refused")
    if info.st_size > max_file_bytes:
        raise DumpSourceError("DD-021")
    device = int(info.st_dev or 0)
    inode = int(info.st_ino or 0)
    if device == 0 and inode == 0:
        raise DumpSourceError("source_refused")
    stream = _open_source(path)
    try:
        current = os.fstat(stream.fileno())
    except OSError as error:
        stream.close()
        raise DumpSourceError("source_refused") from error
    if not _same_stat(info, current):
        stream.close()
        raise DumpSourceError("DD-019")
    facts = SourceFacts(
        name=sanitized_name(path), size=int(info.st_size), device=device, inode=inode
    )
    return stream, facts


def hash_and_scan(stream: BinaryIO, *, chunk_size: int = SCAN_CHUNK) -> tuple[str, int]:
    digest, size, _saw_posts = _scan(stream, chunk_size=chunk_size, digest=True)
    if digest is None:
        raise DumpSourceError("DD-019")
    return digest, size


def hash_only(stream: BinaryIO, *, chunk_size: int = SCAN_CHUNK) -> tuple[str, int]:
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while True:
        block = stream.read(chunk_size)
        if not block:
            break
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _scan(stream: BinaryIO, *, chunk_size: int, digest: bool) -> tuple[str | None, int, bool]:
    stream.seek(0)
    hasher = hashlib.sha256() if digest else None
    size = 0
    carry = b""
    saw_posts = False
    while True:
        block = stream.read(chunk_size)
        if not block:
            break
        if hasher is not None:
            hasher.update(block)
        size += len(block)
        window = carry + block
        lowered = window.lower()
        if any(token in lowered for token in FORBIDDEN_XML_TOKENS):
            raise DumpSourceError("DD-023")
        if _ROOT in lowered:
            saw_posts = True
        carry = window[-forbidden_overlap() :]
    if not saw_posts:
        raise DumpSourceError("DD-002")
    return (None if hasher is None else hasher.hexdigest(), size, saw_posts)


def _same_stat(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        int(first.st_dev or 0) == int(second.st_dev or 0)
        and int(first.st_ino or 0) == int(second.st_ino or 0)
        and int(first.st_size) == int(second.st_size)
    )


def _open_source(path: Path) -> BinaryIO:
    flags = os.O_RDONLY
    if os.name == "posix" and hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise DumpSourceError("source_refused") from error
    return os.fdopen(descriptor, "rb")
