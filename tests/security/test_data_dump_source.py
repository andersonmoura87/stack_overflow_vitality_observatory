"""Fail-closed source checks for offline Data Dump ingest."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from stackoverflow_vitality.data_dump.ingest import ingest_data_dump
from stackoverflow_vitality.data_dump.source import (
    FORBIDDEN_XML_TOKENS,
    DumpSourceError,
    forbidden_overlap,
    hash_and_scan,
    inspect_source,
    open_checked,
)
from tests.dump_fixtures import clock, config, question, write_posts


def test_dtd_and_entity_are_refused_before_parsing(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Posts.xml"
    source.write_text(
        '<!DOCTYPE posts [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n<posts></posts>\n',
        encoding="utf-8",
    )

    def forbidden(_path: Path):
        raise AssertionError("parser must not read a DTD")

    monkeypatch.setattr("stackoverflow_vitality.data_dump.ingest.iter_rows", forbidden)
    with pytest.raises(DumpSourceError) as error:
        ingest_data_dump(
            config(tmp_path),
            source,
            (1,),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
        )
    assert error.value.code == "DD-023"
    assert list((tmp_path / "bronze").rglob("*.json")) == []


def test_symlink_directory_and_reparse_refusal(tmp_path: Path) -> None:
    target = tmp_path / "real.xml"
    write_posts(target, [question(1)])
    link = tmp_path / "link.xml"
    try:
        link.symlink_to(target)
    except OSError:
        link = None
    if link is not None:
        with pytest.raises(DumpSourceError):
            inspect_source(link, max_file_bytes=10**6)
    with pytest.raises(DumpSourceError):
        inspect_source(tmp_path, max_file_bytes=10**6)
    with pytest.raises(DumpSourceError):
        inspect_source(target, max_file_bytes=10**6, attributes=lambda _path: 0x400)
    with pytest.raises(DumpSourceError):
        inspect_source(target, max_file_bytes=10**6, attributes=lambda _path: 0xFFFFFFFF)


def test_missing_windows_api_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import ctypes

    import stackoverflow_vitality.data_dump.source as source_mod

    monkeypatch.setattr(source_mod.os, "name", "nt")
    monkeypatch.delattr(ctypes, "windll", raising=False)
    with pytest.raises(DumpSourceError) as error:
        source_mod.windows_attributes(tmp_path / "Posts.xml")
    assert error.value.code == "source_refused"


def test_null_byte_site_escape_and_output_limit(tmp_path: Path) -> None:
    with pytest.raises(DumpSourceError):
        inspect_source(Path("posts\x00.xml"), max_file_bytes=10)
    with pytest.raises(ValueError):
        config(tmp_path, site="../stackoverflow")
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1, Body="x" * 50)])
    limited = ingest_data_dump(
        config(tmp_path, max_body_chars=10),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert limited["status"] == "partial"
    assert limited["counts"]["rejected_records"] == 1
    output = ingest_data_dump(
        config(tmp_path / "out", max_output_bytes=20),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert output["failure_code"] == "DD-021"


def _assert_refused(payload: bytes, chunk_size: int | None = None) -> None:
    import io

    size = forbidden_overlap() if chunk_size is None else chunk_size
    with pytest.raises(DumpSourceError) as error:
        hash_and_scan(io.BytesIO(payload), chunk_size=size)
    assert error.value.code == "DD-023"


def test_overlap_comes_from_the_forbidden_token_collection() -> None:
    assert forbidden_overlap() == max(len(token) for token in FORBIDDEN_XML_TOKENS) - 1
    longest = max(FORBIDDEN_XML_TOKENS, key=len)
    assert len(longest) == forbidden_overlap() + 1
    _assert_refused(b"<?xml version='1.0'?>\n" + longest + b"\n<posts></posts>\n", chunk_size=1)


def test_dtd_tokens_are_refused_across_chunk_boundaries() -> None:
    _assert_refused(b"<!DOCTYPE posts>\n<posts></posts>\n")
    _assert_refused(b'<!DOCTYPE posts [<!ENTITY local "boom">]>\n<posts></posts>\n')
    _assert_refused(
        b'<!DOCTYPE posts [<!ENTITY remote SYSTEM "http://127.0.0.1/secret">]>\n<posts></posts>\n'
    )
    _assert_refused(b"\xef\xbb\xbf<!DOCTYPE posts>\n<posts></posts>\n")
    _assert_refused(b" \n\t<!DOCTYPE posts>\n<posts></posts>\n")
    long_comment = b"<?xml version='1.0' encoding='utf-8'?>\n<!--" + (b"a" * 300) + b"-->\n"
    _assert_refused(long_comment + b"<!DOCTYPE posts>\n<posts></posts>\n")
    header = b"<?xml version='1.0'?>\n"
    overlap = forbidden_overlap()
    pad = b" " * ((overlap - 1 - (len(header) % overlap)) % overlap)
    assert (len(header) + len(pad)) % overlap == overlap - 1
    _assert_refused(header + pad + b"<!DOCTYPE posts>\n<posts></posts>\n", chunk_size=overlap)


def test_long_prolog_without_dtd_is_scanned_and_unexpected_root_stops_first() -> None:
    import io

    payload = b"<?xml version='1.0'?>\n<!--" + (b"a" * 300) + b"-->\n<posts></posts>\n"
    digest, size = hash_and_scan(io.BytesIO(payload), chunk_size=forbidden_overlap())
    assert size == len(payload)
    assert len(digest) == 64
    with pytest.raises(DumpSourceError) as error:
        hash_and_scan(io.BytesIO(b"<notes></notes>"), chunk_size=1)
    assert error.value.code == "DD-002"


def test_unexpected_root_and_invalid_encoding_do_not_publish(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Posts.xml"
    source.write_text("<notes></notes>", encoding="utf-8")

    def forbidden(_stream: object) -> None:
        raise AssertionError("parser must not read an unexpected root")

    monkeypatch.setattr("stackoverflow_vitality.data_dump.ingest.iter_rows", forbidden)
    with pytest.raises(DumpSourceError) as error:
        ingest_data_dump(
            config(tmp_path),
            source,
            (1,),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
        )
    assert error.value.code == "DD-002"
    monkeypatch.undo()
    source.write_bytes(
        b"<?xml version='1.0' encoding='utf-8'?>\n<posts><row Id='1' Body='\xff'/></posts>\n"
    )
    failed = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert failed["failure_code"] == "DD-001"
    assert list((tmp_path / "bronze" / "data_dump" / "manifests").glob("*.json")) == []


def test_checked_open_uses_one_descriptor_and_matching_fstat(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    stream, facts = open_checked(source, max_file_bytes=10**6)
    try:
        current = os.fstat(stream.fileno())
        assert current.st_size == facts.size
        assert facts.name == "Posts.xml"
    finally:
        stream.close()


def test_posix_open_requests_nofollow(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    import stackoverflow_vitality.data_dump.source as source_mod

    monkeypatch.setattr(source_mod.os, "name", "posix")
    monkeypatch.setattr(source_mod.os, "O_NOFOLLOW", 0x20000, raising=False)
    seen: list[int] = []
    real_open = os.open

    def spy(path: object, flags: int, *args: object, **kwargs: object) -> int:
        seen.append(flags)
        return real_open(path, os.O_RDONLY)  # type: ignore[arg-type]

    monkeypatch.setattr(source_mod.os, "open", spy)
    inspect_source(source, max_file_bytes=10**6)
    assert seen
    assert seen[0] & 0x20000


def test_directory_junction_is_refused_when_the_host_can_create_one(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    if os.name != "nt":
        pytest.skip("junction is a Windows reparse point")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not junction.exists():
        pytest.skip("this host did not create a junction")
    with pytest.raises(DumpSourceError):
        inspect_source(junction, max_file_bytes=10**6)
