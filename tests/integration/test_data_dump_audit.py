"""Executable proofs for the Phase 5.1 audit. Fixtures only; no network."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

import pytest

from stackoverflow_vitality.data_dump.identity import (
    CONTRACT_VERSION,
    MODE,
    PARSER_VERSION,
    ingestion_identity,
    selection_sha256,
)
from stackoverflow_vitality.data_dump.ingest import ingest_data_dump
from stackoverflow_vitality.data_dump.parse import iter_rows
from stackoverflow_vitality.data_dump.publish import QualityLog, ShardWriter
from stackoverflow_vitality.data_dump.source import hash_and_scan, hash_only
from stackoverflow_vitality.storage.files import LocalLockConflict, canonical
from tests.dump_fixtures import answer, clock, config, question, write_posts


def _linked_shards(root: Path) -> list[Path]:
    base = root / "bronze" / "data_dump" / "site=stackoverflow"
    return sorted(base.glob("g=*/shard=*.jsonl"))


def _generation_manifests(root: Path) -> list[Path]:
    directory = root / "bronze" / "data_dump" / "manifests"
    if not directory.is_dir():
        return []
    return list(directory.glob("*.json"))


def _lock(root: Path) -> Path:
    return root / "bronze" / "data_dump" / "site=stackoverflow" / ".ingest.lock"


def _run(root: Path, rows: list[str], ids: tuple[int, ...], **overrides: object):
    source = root / "Posts.xml"
    write_posts(source, rows)
    loaded = config(root, **overrides)
    result = ingest_data_dump(
        loaded,
        source,
        ids,
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    return source, result


def test_parse_clears_each_row_after_copying_attributes(monkeypatch) -> None:
    payload = (
        b"<?xml version='1.0' encoding='utf-8'?>\n"
        b"<posts><row Id='1' Body='Question body'/></posts>\n"
    )
    cleared: list[str] = []
    real_iterparse = ET.iterparse

    class _Proxy:
        def __init__(self, element: ET.Element) -> None:
            self._element = element

        @property
        def tag(self) -> str:
            return self._element.tag

        @property
        def attrib(self) -> dict[str, str]:
            return self._element.attrib

        def clear(self) -> None:
            cleared.append(self.tag)
            self._element.clear()

    def wrapped(stream, events=("end",)):
        for event, element in real_iterparse(stream, events=events):
            yield event, _Proxy(element)

    monkeypatch.setattr(ET, "iterparse", wrapped)
    rows = list(iter_rows(io.BytesIO(payload)))
    assert rows[0][0] == 1
    assert rows[0][1]["Body"] == "Question body"
    assert cleared == ["row", "posts"]


def test_first_shard_closes_before_the_parse_ends(tmp_path: Path, monkeypatch) -> None:
    total = 12
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(item) for item in range(1, total + 1)])
    closed_at: list[int] = []
    real_rows = iter_rows

    def watching(stream):
        for number, attributes in real_rows(stream):
            yield number, attributes
            matches = list((tmp_path / "bronze").rglob("shard=000001.jsonl"))
            if matches and not closed_at:
                closed_at.append(number)

    flushed: list[int] = []
    real_flush = ShardWriter.flush

    def flush(self: ShardWriter) -> None:
        pending = len(self._lines)
        real_flush(self)
        if pending:
            flushed.append(pending)
            assert self._lines == []

    monkeypatch.setattr("stackoverflow_vitality.data_dump.ingest.iter_rows", watching)
    monkeypatch.setattr(ShardWriter, "flush", flush)
    result = ingest_data_dump(
        config(tmp_path, max_shard_records=1),
        source,
        tuple(range(1, total + 1)),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert closed_at == [2]
    assert closed_at[0] < result["counts"]["rows_read"] == total
    assert flushed
    first = next((tmp_path / "bronze").rglob("shard=000001.jsonl"))
    text = first.read_text(encoding="utf-8")
    assert '"post_id":1' in text
    assert '"post_id":12' not in text
    assert result["status"] == "completed"


def test_quality_log_keeps_only_the_line_being_written(tmp_path: Path) -> None:
    log = QualityLog(tmp_path)
    for number in range(20):
        log.add({"code": "DD-011", "source_row_number": number})
    assert set(vars(log)) == {"path", "count"}
    assert log.count == 20
    assert log.finish() == log.path


def test_source_row_numbers_keep_gaps_and_read_order(tmp_path: Path) -> None:
    _source, result = _run(
        tmp_path,
        [question(9), question(1, Body=""), question(4, PostTypeId="3"), answer(2, 1), question(1)],
        (1,),
    )
    assert result["status"] == "partial"
    lines = [
        json.loads(line)
        for line in next((tmp_path / "bronze").rglob("shard=*.jsonl")).read_text().splitlines()
    ]
    assert [line["source_row_number"] for line in lines] == [4, 5]
    assert [line["post_id"] for line in lines] == [2, 1]
    assert result["counts"]["missing_parents"] == 0
    assert result["counts"]["posts_ignored_filter"] == 1
    assert result["counts"]["posts_skipped_type"] == 1
    assert result["counts"]["rows_read"] == 5


def test_missing_parent_is_decided_only_after_the_file_ends(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [answer(2, 1), question(1)])
    seen_during = {"value": False}
    real_rows = iter_rows

    def watching(stream):
        for item in real_rows(stream):
            yield item
            for path in tmp_path.rglob("items.jsonl"):
                if "DD-008" in path.read_text(encoding="utf-8"):
                    seen_during["value"] = True

    monkeypatch.setattr("stackoverflow_vitality.data_dump.ingest.iter_rows", watching)
    completed = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert seen_during["value"] is False
    assert completed["status"] == "completed"
    assert completed["counts"]["missing_parents"] == 0
    write_posts(source, [answer(2, 1)])
    partial = ingest_data_dump(
        config(tmp_path / "only"),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert partial["status"] == "partial"
    assert partial["counts"]["missing_parents"] == 1
    quarantine = next((tmp_path / "only").rglob("items.jsonl")).read_text(encoding="utf-8")
    assert "DD-008" in quarantine
    assert "Answer body" not in quarantine


def test_identities_match_an_independent_canonical_hash(tmp_path: Path, monkeypatch) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    rows = [question(2), question(1)]
    source_left, first = _run(left, rows, (2, 1))
    _source_right, second = _run(right, rows, (1, 2))
    file_hash = hashlib.sha256(source_left.read_bytes()).hexdigest()
    source_key = hashlib.sha256(
        canonical(
            {
                "site": "stackoverflow",
                "source_sha256": file_hash,
                "source_file_size": source_left.stat().st_size,
            }
        )
    ).hexdigest()
    selected = hashlib.sha256(
        json.dumps([1, 2], separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    loaded = config(left)
    document = {
        "source_identity": source_key,
        "contract_version": CONTRACT_VERSION,
        "parser_version": PARSER_VERSION,
        "mode": MODE,
        "selection_sha256": selected,
        "max_attribute_bytes": loaded.max_attribute_bytes,
        "max_body_chars": loaded.max_body_chars,
        "max_shard_records": loaded.max_shard_records,
        "max_shard_bytes": loaded.max_shard_bytes,
        "dump_publication_id": loaded.publication_id,
        "dump_cutoff": None,
        "dump_cutoff_status": "unknown",
    }
    assert selection_sha256((2, 1)) == selection_sha256((1, 2)) == selected
    assert first["source_identity"] == second["source_identity"] == source_key
    assert first["ingestion_identity"] == second["ingestion_identity"]
    assert first["ingestion_identity"] == hashlib.sha256(canonical(document)).hexdigest()
    left_shard = next((left / "bronze").rglob("shard=*.jsonl")).read_bytes()
    right_shard = next((right / "bronze").rglob("shard=*.jsonl")).read_bytes()
    assert left_shard == right_shard
    assert str(left.resolve()) not in left_shard.decode("utf-8")
    manifest = (_generation_manifests(left)[0]).read_text(encoding="utf-8")
    assert str(left.resolve()) not in manifest
    _other_source, other_site = _run(tmp_path / "site", rows, (1, 2), site="serverfault")
    assert hashlib.sha256(_other_source.read_bytes()).hexdigest() == file_hash
    assert other_site["source_identity"] != first["source_identity"]
    narrower = ingest_data_dump(
        config(tmp_path / "narrow"),
        _write_copy(tmp_path / "narrow", source_left),
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert narrower["ingestion_identity"] != first["ingestion_identity"]
    import stackoverflow_vitality.data_dump.identity as identity_mod

    base = ingestion_identity(**_identity_kwargs(source_key, selected))
    monkeypatch.setattr(identity_mod, "PARSER_VERSION", "9.9.9")
    assert ingestion_identity(**_identity_kwargs(source_key, selected)) != base
    monkeypatch.setattr(identity_mod, "PARSER_VERSION", PARSER_VERSION)
    monkeypatch.setattr(identity_mod, "CONTRACT_VERSION", "other.v1")
    assert ingestion_identity(**_identity_kwargs(source_key, selected)) != base
    monkeypatch.setattr(identity_mod, "CONTRACT_VERSION", CONTRACT_VERSION)
    cutoff, _cutoff_result = _run(
        tmp_path / "cutoff",
        rows,
        (1, 2),
        dump_cutoff="2021-01-01T00:00:00Z",
    )
    assert hashlib.sha256(cutoff.read_bytes()).hexdigest() == file_hash
    cutoff_result = json.loads(_generation_manifests(tmp_path / "cutoff")[0].read_text())
    assert cutoff_result["ingestion_identity"] != first["ingestion_identity"]
    same_operational, _same = _run(tmp_path / "ops", rows, (1, 2), max_records=50)
    assert (
        json.loads(_generation_manifests(tmp_path / "ops")[0].read_text())["ingestion_identity"]
        == first["ingestion_identity"]
    )
    _run(tmp_path / "shards", rows, (1, 2), max_shard_records=1)
    changed = json.loads(_generation_manifests(tmp_path / "shards")[0].read_text())
    assert changed["ingestion_identity"] != first["ingestion_identity"]
    assert same_operational.read_bytes() == source_left.read_bytes()


def test_shard_rotation_stops_before_the_byte_limit(tmp_path: Path) -> None:
    record = {"post_id": 1, "body": "a"}
    line = canonical(record) + b"\n"
    writer = ShardWriter(tmp_path / "stage", 10, len(line), 10**6)
    writer.add(record, 1)
    writer.add({"post_id": 2, "body": "b"}, 2)
    shards = writer.close()
    assert [shard.records for shard in shards] == [1, 1]
    assert shards[0].size_bytes <= len(line)
    assert shards[0].name == "shard=000001.jsonl"
    assert shards[0].first_post_id == 1
    assert shards[1].last_post_id == 2
    assert ShardWriter(tmp_path / "empty", 10, 100, 100).close() == ()
    huge = ShardWriter(tmp_path / "huge", 10, len(line) - 1, 10**6)
    with pytest.raises(Exception) as error:
        huge.add(record, 1)
    assert getattr(error.value, "code", None) == "DD-021"
    assert list((tmp_path / "huge").glob("shard=*.jsonl")) == []


def test_one_descriptor_serves_checksum_parse_and_final_checksum(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    opens: list[int] = []
    real_open = __import__("os").open

    def spy(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(str(path)).resolve() == source.resolve():
            opens.append(flags)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("os.open", spy)

    def fail_hash(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not hash the source")

    monkeypatch.setattr("stackoverflow_vitality.data_dump.ingest.hash_and_scan", fail_hash)
    monkeypatch.setattr("stackoverflow_vitality.data_dump.ingest.hash_only", fail_hash)
    monkeypatch.setattr(
        "stackoverflow_vitality.data_dump.ingest.iter_rows",
        lambda _stream: (_ for _ in ()).throw(AssertionError("dry-run must not parse")),
    )
    dry = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
        dry_run=True,
    )
    assert dry["source_identity_status"] == "pending_source_checksum"
    assert dry["ingestion_identity_status"] == "pending_source_checksum"
    assert not (tmp_path / "bronze").exists()
    assert len(opens) == 1
    monkeypatch.undo()
    opens.clear()
    monkeypatch.setattr("os.open", spy)
    result = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert result["status"] == "completed"
    assert len(opens) == 1
    with source.open("rb") as stream:
        digest, size = hash_and_scan(stream)
        again, again_size = hash_only(stream)
    assert (digest, size) == (again, again_size)


def test_crash_points_release_the_lock_and_retry_is_deterministic(
    tmp_path: Path, monkeypatch
) -> None:
    import stackoverflow_vitality.data_dump.ingest as ingest_mod

    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1), question(2)])
    loaded = config(tmp_path, max_shard_records=1)

    def ingest() -> dict:
        return ingest_data_dump(
            loaded,
            source,
            (1, 2),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
        )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("before link")

    monkeypatch.setattr(ingest_mod, "link_confirmed", boom)
    with pytest.raises(RuntimeError, match="before link"):
        ingest()
    assert _generation_manifests(tmp_path) == []
    assert not _lock(tmp_path).exists()
    monkeypatch.undo()
    real_link = ingest_mod.link_confirmed
    calls = {"count": 0}

    def after_first(origin: Path, destination: Path) -> None:
        calls["count"] += 1
        if calls["count"] > 1:
            raise RuntimeError("after first link")
        real_link(origin, destination)

    monkeypatch.setattr(ingest_mod, "link_confirmed", after_first)
    with pytest.raises(RuntimeError, match="after first link"):
        ingest()
    kept = _linked_shards(tmp_path)[0].read_bytes()
    assert _generation_manifests(tmp_path) == []
    assert not _lock(tmp_path).exists()
    monkeypatch.undo()
    finished = ingest()
    assert finished["status"] == "completed"
    linked = _linked_shards(tmp_path)
    assert linked[0].read_bytes() == kept
    assert len(linked) == 2
    manifest = _generation_manifests(tmp_path)[0]
    original = manifest.read_bytes()
    published = json.loads(original)["published_at"]

    real_publish = ingest_mod.publish

    def after_manifest(path: Path, content: bytes, immutable: bool = True) -> None:
        real_publish(path, content, immutable=immutable)
        if "runs" not in path.parts:
            raise RuntimeError("after manifest")

    other = tmp_path / "after"
    write_posts(other / "Posts.xml", [question(1)])
    monkeypatch.setattr(ingest_mod, "publish", after_manifest)
    with pytest.raises(RuntimeError, match="after manifest"):
        ingest_data_dump(
            config(other),
            other / "Posts.xml",
            (1,),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
        )
    assert not _lock(other).exists()
    monkeypatch.undo()
    replay = ingest_data_dump(
        config(other),
        other / "Posts.xml",
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert replay["replay"] is True
    assert (
        replay["published_at"]
        == json.loads(_generation_manifests(other)[0].read_text(encoding="utf-8"))["published_at"]
    )
    assert manifest.read_bytes() == original
    assert published == json.loads(original)["published_at"]

    def broken_record(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("attempt record")

    bad = tmp_path / "bad"
    (bad / "Posts.xml").parent.mkdir(parents=True)
    (bad / "Posts.xml").write_text("<posts><row></posts>", encoding="utf-8")
    monkeypatch.setattr(ingest_mod, "_store_run", broken_record)
    with pytest.raises(RuntimeError, match="attempt record"):
        ingest_data_dump(
            config(bad),
            bad / "Posts.xml",
            (1,),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
        )
    assert not _lock(bad).exists()
    monkeypatch.undo()
    write_posts(bad / "Posts.xml", [question(1)])
    recovered = ingest_data_dump(
        config(bad),
        bad / "Posts.xml",
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert recovered["status"] == "completed"


def test_interrupted_publish_is_not_a_replay_until_the_manifest_exists(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1), question(2)])
    loaded = config(tmp_path, max_shard_records=1)

    def interrupt() -> None:
        raise RuntimeError("before manifest")

    with pytest.raises(RuntimeError, match="before manifest"):
        ingest_data_dump(
            loaded,
            source,
            (1, 2),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
            before_manifest=interrupt,
        )
    assert _generation_manifests(tmp_path) == []
    assert not _lock(tmp_path).exists()
    shards = _linked_shards(tmp_path)
    assert len(shards) == 2
    before = [path.read_bytes() for path in shards]
    completed = ingest_data_dump(
        loaded,
        source,
        (1, 2),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert completed["status"] == "completed"
    assert completed.get("replay") is not True
    assert [path.read_bytes() for path in _linked_shards(tmp_path)] == before
    assert len(_generation_manifests(tmp_path)) == 1


def test_site_lock_rejects_a_second_writer_without_waiting(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    entered = threading.Event()
    release = threading.Event()
    outcome: dict[str, object] = {}

    def hold() -> None:
        entered.set()
        assert release.wait(5)

    def first() -> None:
        outcome["first"] = ingest_data_dump(
            config(tmp_path),
            source,
            (1,),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
            before_manifest=hold,
        )

    def second() -> None:
        assert entered.wait(5)
        try:
            ingest_data_dump(
                config(tmp_path),
                source,
                (1,),
                run_id=str(uuid4()),
                now=clock,
                disk_free=lambda _path: 10**12,
            )
            outcome["second"] = "ran"
        except LocalLockConflict:
            outcome["second"] = "refused"
        finally:
            release.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert outcome["second"] == "refused"
    assert outcome["first"]["status"] == "completed"  # type: ignore[index]
    assert not _lock(tmp_path).exists()


def test_limits_and_empty_selection_do_not_publish_a_false_success(tmp_path: Path) -> None:
    source, limited = _run(tmp_path / "limit", [question(1), question(2)], (1, 2), max_records=1)
    assert limited["status"] == "failed"
    assert limited["failure_code"] == "DD-020"
    assert _generation_manifests(tmp_path / "limit") == []
    assert not _lock(tmp_path / "limit").exists()
    _source, empty = _run(tmp_path / "empty", [question(1)], (9,))
    assert empty["status"] == "empty"
    assert empty["counts"]["posts_emitted"] == 0
    assert list((tmp_path / "empty" / "bronze").rglob("shard=*.jsonl")) == []
    assert len(_generation_manifests(tmp_path / "empty")) == 1
    assert source.is_file()


def test_logs_omit_bodies_and_absolute_paths(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    caplog.set_level(logging.INFO, logger="stackoverflow_vitality.data_dump")
    result = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    events = [json.loads(record.message)["event"] for record in caplog.records]
    for name in (
        "dump_start",
        "dump_source_validated",
        "dump_checksum",
        "dump_rows_read",
        "dump_questions_selected",
        "dump_shard_prepared",
        "dump_shard_confirmed",
        "dump_generation_published",
        "dump_finished",
    ):
        assert name in events
    text = "\n".join(record.message for record in caplog.records)
    assert "Question body" not in text
    assert "<posts" not in text
    assert str(source.resolve()) not in text
    assert result["counts"]["rows_read"] == 1
    assert result["api_comparison"] == "skipped"


def _identity_kwargs(source_key: str, selected: str) -> dict[str, object]:
    return {
        "source": source_key,
        "selection": selected,
        "max_attribute_bytes": 1024**2,
        "max_body_chars": 500_000,
        "max_shard_records": 5_000,
        "max_shard_bytes": 64 * 1024**2,
        "dump_publication_id": "2026-03",
        "dump_cutoff": None,
        "dump_cutoff_status": "unknown",
    }


def _write_copy(root: Path, source: Path) -> Path:
    destination = root / "Posts.xml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    return destination
