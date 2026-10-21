"""Unit coverage for offline Data Dump ingest."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from stackoverflow_vitality.cli import main
from stackoverflow_vitality.data_dump.config import load_dump_config
from stackoverflow_vitality.data_dump.identity import ingestion_identity, source_identity
from stackoverflow_vitality.data_dump.ingest import ingest_data_dump, load_selection
from stackoverflow_vitality.data_dump.quality import content_fields
from stackoverflow_vitality.data_dump.source import DumpSourceError, inspect_source
from stackoverflow_vitality.storage.files import LocalLockConflict, exclusive_lock
from tests.dump_fixtures import answer, clock, config, question, write_posts


def _run(tmp_path: Path, rows: list[str], ids: tuple[int, ...] = (1,), **overrides: object):
    source = tmp_path / "Posts.xml"
    write_posts(source, rows)
    loaded = config(tmp_path, **overrides)
    result = ingest_data_dump(
        loaded, source, ids, run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    return source, loaded, result


def test_config_precedence_and_secret_rejection(tmp_path: Path) -> None:
    path = tmp_path / "dump.yml"
    path.write_text(
        "site: serverfault\npublication_id: from-yaml\nmin_free_bytes: 1\n", encoding="utf-8"
    )
    overridden = load_dump_config(
        path, {"STACKEXCHANGE_DUMP_SITE": "superuser"}, {"site": "stackoverflow"}
    )
    from_env = load_dump_config(path, {"STACKEXCHANGE_DUMP_SITE": "superuser"}, {})
    from_yaml = load_dump_config(path, {}, {})
    assert overridden.site == "stackoverflow"
    assert from_env.site == "superuser"
    assert from_yaml.site == "serverfault"
    assert from_yaml.publication_id == "from-yaml"
    path.write_text("api_key: secret\npublication_id: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="secrets"):
        load_dump_config(path, {}, {})
    path.write_text("mode: full\npublication_id: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="restricted"):
        load_dump_config(path, {}, {})


def test_operational_parameters_do_not_change_identity() -> None:
    common = {
        "source": "a" * 64,
        "selection": "b" * 64,
        "max_attribute_bytes": 10,
        "max_body_chars": 20,
        "max_shard_records": 5,
        "max_shard_bytes": 100,
        "dump_publication_id": "2026-03",
        "dump_cutoff": None,
        "dump_cutoff_status": "unknown",
    }
    assert ingestion_identity(**common) == ingestion_identity(**common)
    changed = dict(common)
    changed["max_shard_bytes"] = 101
    assert ingestion_identity(**changed) != ingestion_identity(**common)
    assert source_identity("stackoverflow", "abc", 4) == source_identity("stackoverflow", "abc", 4)


def test_selection_rejects_duplicates_and_bools(tmp_path: Path) -> None:
    path = tmp_path / "ids.json"
    path.write_text("[1, 1]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_selection(path)
    path.write_text("[true]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_selection(path)
    path.write_text('{"question_ids": [2, 1]}', encoding="utf-8")
    assert load_selection(path) == (1, 2)


def test_source_refuses_directory_null_and_unreadable_reparse(tmp_path: Path) -> None:
    with pytest.raises(DumpSourceError):
        inspect_source(tmp_path, max_file_bytes=10)
    with pytest.raises(DumpSourceError):
        inspect_source(Path("a\x00b"), max_file_bytes=10)
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    with pytest.raises(DumpSourceError) as refused:
        inspect_source(source, max_file_bytes=10, attributes=lambda _path: 0xFFFFFFFF)
    assert refused.value.code == "source_refused"
    with pytest.raises(DumpSourceError) as limited:
        inspect_source(source, max_file_bytes=1)
    assert limited.value.code == "DD-021"


def test_minimal_row_keeps_nulls_and_rejects_bool_score(tmp_path: Path) -> None:
    _source, _config, result = _run(
        tmp_path, [question(1, Score="", OwnerUserId="", ContentLicense="")]
    )
    assert result["counts"]["rejected_records"] == 1
    _source, _config, result = _run(tmp_path, [question(1, Score="true")])
    assert result["failure_code"] is None
    assert result["counts"]["rejected_records"] == 1
    shard = next((tmp_path / "bronze").rglob("shard=*.jsonl"), None)
    assert shard is None


def test_filter_read_order_and_warnings(tmp_path: Path) -> None:
    rows = [
        answer(8, 1),
        question(1, OwnerDisplayName="DisplayNameShouldNotPersist"),
        question(2),
        '<row Id="3" PostTypeId="4" Body="ignored" />'.removeprefix("<row ").removesuffix(" />"),
    ]
    _source, _config, result = _run(tmp_path, rows, (1,))
    assert result["status"] == "completed"
    assert result["counts"]["posts_ignored_filter"] == 1
    assert result["counts"]["posts_skipped_type"] == 1
    assert result["counts"]["missing_parents"] == 0
    shard = next((tmp_path / "bronze").rglob("shard=*.jsonl"))
    lines = [json.loads(line) for line in shard.read_text(encoding="utf-8").splitlines()]
    assert [line["post_id"] for line in lines] == [8, 1]
    assert [line["source_row_number"] for line in lines] == [1, 2]
    assert lines[1]["tags_raw"] == "<python><xml>"
    assert lines[1]["score"] == 3
    assert lines[1]["comment_count"] == 2
    assert "DisplayNameShouldNotPersist" not in shard.read_text(encoding="utf-8")
    assert result["shards"][0]["first_post_id"] == 8
    assert result["shards"][0]["last_post_id"] == 1
    assert result["api_comparison"] == "skipped"
    assert str(tmp_path) not in json.dumps(result)


def test_each_quarantine_code(tmp_path: Path) -> None:
    cases = {
        "DD-003": [question(1).replace('Id="1"', 'Id="0"', 1)],
        "DD-004": [question(1).replace('PostTypeId="1"', 'PostTypeId="no"', 1)],
        "DD-006": ['Id="8" PostTypeId="2" Body="Answer body" CreationDate="2020-01-03T03:04:05"'],
        "DD-009": [question(1, CreationDate="yesterday")],
        "DD-010": [question(1, CreationDate="2007-12-31T23:59:59")],
        "DD-011": [question(1, Body=" ")],
        "DD-012": [question(1, Body="too long")],
        "DD-013": [question(1, Score="true")],
    }
    for code, rows in cases.items():
        folder = tmp_path / code
        folder.mkdir()
        overrides: dict[str, object] = {}
        if code == "DD-012":
            overrides["max_body_chars"] = 3
        _source, _config, result = _run(folder, rows, **overrides)
        assert result["status"] == "partial", code
        assert result["counts"]["rejected_records"] == 1
        quality = next((folder / "quarantine").rglob("items.jsonl"))
        assert json.loads(quality.read_text(encoding="utf-8").splitlines()[0])["code"] == code


def test_duplicate_rules_and_missing_parent(tmp_path: Path) -> None:
    _source, _config, identical = _run(tmp_path / "same", [question(1), question(1)])
    assert identical["status"] == "completed"
    assert identical["counts"]["duplicates_identical"] == 1
    assert identical["counts"]["posts_emitted"] == 1
    _source, _config, divergent = _run(
        tmp_path / "different", [question(1, Body="one"), question(1, Body="two")]
    )
    assert divergent["status"] == "failed"
    assert divergent["failure_code"] == "DD-017"
    assert (
        list((tmp_path / "different" / "bronze" / "data_dump" / "manifests").glob("*.json")) == []
    )
    _source, _config, missing = _run(tmp_path / "parent", [answer(8, 1)], (1,))
    assert missing["status"] == "partial"
    assert missing["counts"]["missing_parents"] == 1
    assert missing["counts"]["answers_emitted"] == 1


def test_limits_disk_and_record_ceiling(tmp_path: Path) -> None:
    _source, _config, records = _run(
        tmp_path / "records", [question(1), question(2)], (1, 2), max_records=1
    )
    assert records["failure_code"] == "DD-020"
    _source, _config, output = _run(tmp_path / "output", [question(1)], max_output_bytes=32)
    assert output["failure_code"] == "DD-021"
    _source, _config, attribute = _run(
        tmp_path / "attribute", [question(1, Body="x" * 40)], max_attribute_bytes=16
    )
    assert attribute["failure_code"] == "DD-021"
    source = tmp_path / "disk" / "Posts.xml"
    source.parent.mkdir()
    write_posts(source, [question(1)])
    disk = ingest_data_dump(
        config(tmp_path / "disk"),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 0,
    )
    assert disk["failure_code"] == "DD-022"

    def unavailable(_path: Path) -> int:
        raise OSError("unavailable")

    unknown = ingest_data_dump(
        config(tmp_path / "unknown"),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=unavailable,
    )
    assert unknown["failure_code"] == "DD-022"


def test_replay_conflict_and_lock(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    loaded = config(tmp_path)
    first = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    manifest = (
        tmp_path / "bronze" / "data_dump" / "manifests" / f"{first['ingestion_identity']}.json"
    )
    original = manifest.read_bytes()
    second = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    assert second["replay"] is True
    assert manifest.read_bytes() == original
    shard = next((tmp_path / "bronze").rglob("shard=*.jsonl"))
    shard.write_bytes(shard.read_bytes() + b" ")
    conflict = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    assert conflict["failure_code"] == "conflict"
    assert manifest.read_bytes() == original
    lock = tmp_path / "bronze" / "data_dump" / "site=stackoverflow" / ".ingest.lock"
    with exclusive_lock(lock):
        with pytest.raises(LocalLockConflict):
            ingest_data_dump(
                loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
            )
    assert not lock.exists()


def test_owner_and_license_warnings_can_complete(tmp_path: Path) -> None:
    _source, _config, result = _run(
        tmp_path,
        [question(1).replace(' OwnerUserId="7"', "").replace(' ContentLicense="CC BY-SA 4.0"', "")],
    )
    assert result["status"] == "completed"
    assert result["readable"] is True
    assert result["consumable"] is True
    assert result["counts"]["owner_absent"] == 1
    assert result["counts"]["license_absent"] == 1
    assert result["counts"]["rejected_records"] == 0


def test_api_conflict_helper_is_not_used_by_ingest() -> None:
    fields = content_fields(
        {"site": "stackoverflow", "post_id": 1, "body": "a", "score": 1},
        {"site": "stackoverflow", "post_id": 1, "body": "b", "score": 1},
    )
    assert fields == ("body",)
    assert (
        content_fields(
            {"site": "stackoverflow", "post_id": 1, "body": "a", "score": 1},
            {"site": "stackoverflow", "post_id": 1, "body": "a", "score": 1},
        )
        == ()
    )
    with pytest.raises(ValueError):
        content_fields(
            {"site": "stackoverflow", "post_id": 1}, {"site": "stackoverflow", "post_id": 2}
        )


def test_empty_unexpected_element_and_shard_split(tmp_path: Path) -> None:
    empty_source = tmp_path / "empty" / "Posts.xml"
    empty_source.parent.mkdir()
    empty_source.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<posts></posts>\n', encoding="utf-8"
    )
    empty = ingest_data_dump(
        config(empty_source.parent),
        empty_source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert empty["status"] == "empty"
    assert list((empty_source.parent / "bronze").rglob("shard=*.jsonl")) == []
    broken = tmp_path / "broken" / "Posts.xml"
    broken.parent.mkdir()
    broken.write_text('<?xml version="1.0"?>\n<posts><note/></posts>\n', encoding="utf-8")
    refused = ingest_data_dump(
        config(broken.parent),
        broken,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert refused["failure_code"] == "DD-002"
    _source, _config, split = _run(
        tmp_path / "split", [question(1), question(2)], (1, 2), max_shard_records=1
    )
    assert [shard["name"] for shard in split["shards"]] == [
        "shard=000001.jsonl",
        "shard=000002.jsonl",
    ]
    assert split["shards"][0]["first_post_id"] == 1
    assert split["shards"][1]["last_post_id"] == 2


def test_relative_path_cutoff_and_rss_ceiling(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "Posts.xml"
    write_posts(source, [question(1)])
    loaded = config(
        tmp_path,
        bronze_output=str(tmp_path / "bronze"),
        quarantine_output=str(tmp_path / "quarantine"),
        record_relative_path=True,
        input_root=str(tmp_path / "inbox"),
    )
    result = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    assert result["parameters"]["source_relative_path"] == "Posts.xml"
    assert str(source.resolve()) not in json.dumps(result)
    future = config(tmp_path / "future", dump_cutoff="2999-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="after source inspection"):
        ingest_data_dump(
            future,
            source,
            (1,),
            run_id=str(uuid4()),
            now=clock,
            disk_free=lambda _path: 10**12,
        )
    rss = ingest_data_dump(
        config(tmp_path / "rss", max_rss_bytes=1),
        source,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
        rss_bytes=lambda: None,
    )
    assert rss["failure_code"] == "DD-021"


def test_invalid_optional_fields_and_config_edges(tmp_path: Path) -> None:
    _source, _config, parent = _run(tmp_path / "parent-id", [question(1, ParentId="no")])
    assert parent["status"] == "partial"
    _source, _config, accepted = _run(tmp_path / "accepted", [question(1, AcceptedAnswerId="no")])
    assert accepted["counts"]["rejected_records"] == 1
    _source, _config, views = _run(tmp_path / "views", [question(1, ViewCount="no")])
    assert views["counts"]["rejected_records"] == 1
    _source, _config, missing_id = _run(
        tmp_path / "missing-id",
        ['PostTypeId="1" Body="Question body" CreationDate="2020-01-02T03:04:05"'],
    )
    assert missing_id["counts"]["rejected_records"] == 1
    _source, _config, fraction = _run(
        tmp_path / "fraction", [question(1, CreationDate="2020-01-02T03:04:05.5")]
    )
    line = json.loads(next((tmp_path / "fraction" / "bronze").rglob("shard=*.jsonl")).read_text())
    assert line["source_created_at"] == "2020-01-02T03:04:05.500000Z"
    document = tmp_path / "dump.yml"
    document.write_text("publication_id: 2026-03\ndump_cutoff: yesterday\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_dump_config(document, {}, {})
    document.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_dump_config(document, {}, {})
    with pytest.raises(ValueError):
        load_dump_config(tmp_path / "missing.yml", {}, {})
    plain = tmp_path / "plain.xml"
    kept = tmp_path / "kept.xml"
    write_posts(kept, [question(1)])
    with pytest.raises(DumpSourceError) as missing_root:
        inspect_source(tmp_path / "missing.yml", max_file_bytes=10)
    assert missing_root.value.code == "source_refused"
    plain.write_text("not xml", encoding="utf-8")
    with pytest.raises(DumpSourceError) as root:
        inspect_source(plain, max_file_bytes=10**6)
    assert root.value.code == "DD-002"
    facts = inspect_source(kept, max_file_bytes=10**6, attributes=lambda _path: None)
    assert facts.name == "kept.xml"


def test_cli_publishes_and_rejects_a_null_path(tmp_path: Path, capsys) -> None:
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
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["readable"] is True
    assert payload["consumable"] is True
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "ingest-data-dump",
                "--config",
                str(document),
                "--source",
                "a\x00.xml",
                "--question-ids-file",
                str(selection),
            ]
        )
    assert raised.value.code == 2


def _cli_document(tmp_path: Path) -> Path:
    document = tmp_path / "dump.yml"
    document.parent.mkdir(parents=True, exist_ok=True)
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
    return document


def test_cli_partial_is_readable_and_not_operational_success(tmp_path: Path, capsys) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [answer(2, 1)])
    selection = tmp_path / "ids.json"
    selection.write_text("[1]", encoding="utf-8")
    document = _cli_document(tmp_path)
    exit_code = main(
        [
            "ingest-data-dump",
            "--config",
            str(document),
            "--source",
            str(source),
            "--question-ids-file",
            str(selection),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert payload["status"] == "partial"
    assert payload["readable"] is True
    assert payload["consumable"] is False
    manifest = next((tmp_path / "bronze" / "data_dump" / "manifests").glob("*.json"))
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert stored["readable"] is True
    assert stored["consumable"] is False
    assert list((tmp_path / "bronze").rglob("shard=*.jsonl"))
    original = manifest.read_bytes()
    replay = main(
        [
            "ingest-data-dump",
            "--config",
            str(document),
            "--source",
            str(source),
            "--question-ids-file",
            str(selection),
        ]
    )
    replay_payload = json.loads(capsys.readouterr().out)
    assert replay == 3
    assert replay_payload["replay"] is True
    assert replay_payload["consumable"] is False
    assert manifest.read_bytes() == original


def test_cli_empty_succeeds_and_failed_limit_does_not_publish(tmp_path: Path, capsys) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(source, [question(1)])
    selection = tmp_path / "ids.json"
    selection.write_text("[9]", encoding="utf-8")
    document = _cli_document(tmp_path)
    exit_code = main(
        [
            "ingest-data-dump",
            "--config",
            str(document),
            "--source",
            str(source),
            "--question-ids-file",
            str(selection),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "empty"
    assert payload["readable"] is True
    assert payload["consumable"] is True
    assert list((tmp_path / "bronze").rglob("shard=*.jsonl")) == []
    assert list((tmp_path / "bronze" / "data_dump" / "manifests").glob("*.json"))
    limited = main(
        [
            "ingest-data-dump",
            "--config",
            str(document),
            "--source",
            str(source),
            "--question-ids-file",
            str(selection),
            "--max-records",
            "1",
        ]
    )
    limited_payload = json.loads(capsys.readouterr().out)
    assert limited == 0
    assert limited_payload["replay"] is True
    write_posts(source, [question(1), question(2)])
    selection.write_text("[1, 2]", encoding="utf-8")
    failed = main(
        [
            "ingest-data-dump",
            "--config",
            str(_cli_document(tmp_path / "limit")),
            "--source",
            str(source),
            "--question-ids-file",
            str(selection),
            "--max-records",
            "1",
        ]
    )
    failed_payload = json.loads(capsys.readouterr().out)
    assert failed == 1
    assert failed_payload["status"] == "failed"
    assert failed_payload["failure_code"] == "DD-020"
    assert failed_payload["readable"] is False
    assert failed_payload["consumable"] is False
    assert list((tmp_path / "limit" / "bronze" / "data_dump" / "manifests").glob("*.json")) == []


def test_cli_refuses_source_lock_unknown_key_and_records_relative_path(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "Posts.xml"
    source.write_text("<!DOCTYPE posts>\n<posts></posts>\n", encoding="utf-8")
    selection = tmp_path / "ids.json"
    selection.write_text("[1]", encoding="utf-8")
    document = _cli_document(tmp_path)
    refused = main(
        [
            "ingest-data-dump",
            "--config",
            str(document),
            "--source",
            str(source),
            "--question-ids-file",
            str(selection),
        ]
    )
    refused_payload = json.loads(capsys.readouterr().out)
    assert refused == 1
    assert refused_payload["failure_code"] == "DD-023"
    assert refused_payload["consumable"] is False
    assert list((tmp_path / "bronze").rglob("*.json")) == []
    write_posts(source, [question(1)])
    lock = tmp_path / "bronze" / "data_dump" / "site=stackoverflow" / ".ingest.lock"
    with exclusive_lock(lock):
        locked = main(
            [
                "ingest-data-dump",
                "--config",
                str(document),
                "--source",
                str(source),
                "--question-ids-file",
                str(selection),
            ]
        )
    locked_payload = json.loads(capsys.readouterr().out)
    assert locked == 1
    assert locked_payload["failure_code"] == "lock_conflict"
    assert not lock.exists()
    unknown = tmp_path / "unknown.yml"
    unknown.write_text("publication_id: 2026-03\nextra: 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "ingest-data-dump",
                "--config",
                str(unknown),
                "--source",
                str(source),
                "--question-ids-file",
                str(selection),
            ]
        )
    assert raised.value.code == 2
    inbox = tmp_path / "inbox"
    relative_source = inbox / "Posts.xml"
    write_posts(relative_source, [question(1)])
    recorded = main(
        [
            "ingest-data-dump",
            "--config",
            str(_cli_document(tmp_path / "relative")),
            "--source",
            str(relative_source),
            "--question-ids-file",
            str(selection),
            "--record-relative-path",
            "--input-root",
            str(inbox),
        ]
    )
    recorded_payload = json.loads(capsys.readouterr().out)
    assert recorded == 0
    assert recorded_payload["parameters"]["source_relative_path"] == "Posts.xml"
    assert str(relative_source.resolve()) not in json.dumps(recorded_payload)
