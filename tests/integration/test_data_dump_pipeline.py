"""Offline integration of staging, publication, replay and interruption."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from stackoverflow_vitality.cli import main
from stackoverflow_vitality.data_dump.ingest import ingest_data_dump
from tests.dump_fixtures import answer, clock, config, question, write_posts


def test_orders_filter_and_untouched_api_state(tmp_path: Path) -> None:
    watermark = tmp_path / "bronze" / "watermarks.json"
    watermark.parent.mkdir()
    watermark.write_text('{"questions":"kept"}\n', encoding="utf-8")
    checkpoint = tmp_path / "bronze" / "answers" / "checkpoint.json"
    checkpoint.parent.mkdir()
    checkpoint.write_text('{"answers":"kept"}\n', encoding="utf-8")
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1), answer(8, 1), question(9), answer(10, 9)])
    result = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert result["status"] == "completed"
    assert result["counts"]["posts_ignored_filter"] == 2
    lines = [
        json.loads(line)
        for line in next((tmp_path / "bronze").rglob("shard=*.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [line["post_id"] for line in lines] == [1, 8]
    assert watermark.read_text(encoding="utf-8") == '{"questions":"kept"}\n'
    assert checkpoint.read_text(encoding="utf-8") == '{"answers":"kept"}\n'


def test_answer_before_question_is_complete(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [answer(8, 1), question(1)])
    result = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert result["status"] == "completed"
    assert result["counts"]["missing_parents"] == 0


def test_malformed_xml_and_unstable_source_do_not_publish(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    source.write_text("<posts><row Id='1'></posts>", encoding="utf-8")
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
    assert (tmp_path / "bronze" / "data_dump" / "manifests" / "runs").is_dir()

    write_posts(source, [question(1)])

    def replace_source() -> None:
        source.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<posts><row Id="1" PostTypeId="1" '
            'Body="other" CreationDate="2020-01-02T03:04:05" Score="1" '
            'OwnerUserId="7" ContentLicense="CC BY-SA 4.0" /></posts>\n',
            encoding="utf-8",
        )

    changed = ingest_data_dump(
        config(tmp_path),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
        after_first_checksum=replace_source,
    )
    assert changed["failure_code"] == "DD-019"
    assert list((tmp_path / "bronze" / "data_dump" / "manifests").glob("*.json")) == []


def test_crash_before_manifest_is_completed_on_the_next_attempt(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    loaded = config(tmp_path)

    def interrupt() -> None:
        raise RuntimeError("interrupted before the terminal manifest")

    with pytest.raises(RuntimeError, match="interrupted"):
        ingest_data_dump(
            loaded,
            source,
            (1,),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
            before_manifest=interrupt,
        )
    shard = next((tmp_path / "bronze").rglob("shard=*.jsonl"))
    original = shard.read_bytes()
    assert list((tmp_path / "bronze" / "data_dump" / "manifests").glob("*.json")) == []
    completed = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    assert completed["status"] == "completed"
    assert shard.read_bytes() == original
    manifest = (
        tmp_path / "bronze" / "data_dump" / "manifests" / f"{completed['ingestion_identity']}.json"
    )
    assert manifest.is_file()


def test_replay_conflict_and_manifest_is_last(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    loaded = config(tmp_path)
    observed: dict[str, bool] = {}

    def before_manifest() -> None:
        observed["shard"] = any((tmp_path / "bronze").rglob("shard=*.jsonl"))
        observed["manifest"] = any((tmp_path / "bronze" / "data_dump" / "manifests").glob("*.json"))

    first = ingest_data_dump(
        loaded,
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
        before_manifest=before_manifest,
    )
    assert observed == {"shard": True, "manifest": False}
    manifest = (
        tmp_path / "bronze" / "data_dump" / "manifests" / f"{first['ingestion_identity']}.json"
    )
    original = manifest.read_bytes()
    replay = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    assert replay["replay"] is True
    assert manifest.read_bytes() == original
    shard = next((tmp_path / "bronze").rglob("shard=*.jsonl"))
    shard.write_bytes(original)
    conflict = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    assert conflict["failure_code"] == "conflict"
    assert manifest.read_bytes() == original


def test_dry_run_prints_pending_identity_and_creates_nothing(tmp_path: Path, capsys) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    selection = tmp_path / "ids.json"
    selection.write_text("[1]", encoding="utf-8")
    document = tmp_path / "dump.yml"
    document.write_text(
        "\n".join(
            [
                "site: stackoverflow",
                "publication_id: 2026-03",
                f"bronze_output: {tmp_path / 'bronze'}",
                f"quarantine_output: {tmp_path / 'quarantine'}",
                "min_free_bytes: 1",
            ]
        ),
        encoding="utf-8",
    )
    exit_code = main(
        [
            "ingest-data-dump",
            "--config",
            str(document),
            "--source",
            str(source),
            "--question-ids-file",
            str(selection),
            "--dry-run",
        ]
    )
    captured_all = capsys.readouterr()
    captured = captured_all.out.splitlines()
    payload = json.loads(captured[0])
    assert exit_code == 0
    assert payload["source_identity_status"] == "pending_source_checksum"
    assert payload["ingestion_identity_status"] == "pending_source_checksum"
    assert "source_identity" not in payload
    assert "ingestion_identity" not in payload
    assert captured[1:] == [
        "source_identity_status=pending_source_checksum",
        "ingestion_identity_status=pending_source_checksum",
    ]
    assert not (tmp_path / "bronze").exists()
    assert str(source.resolve()) not in captured_all.out
    assert str(source.resolve()) not in captured_all.err
