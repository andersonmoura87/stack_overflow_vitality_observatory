"""Synthetic Posts.xml to Data Dump Bronze to Data Dump Silver."""

import json
from pathlib import Path

import pytest

from stackoverflow_vitality.cli import main
from stackoverflow_vitality.data_dump_silver.transform import transform_data_dump
from stackoverflow_vitality.storage.files import canonical
from tests.dump_fixtures import answer, question
from tests.dump_silver import publish_bronze, rewrite_raw, rewrite_shard, shard_path


def test_snapshot_pipeline_sorts_acceptance_and_leaves_api_state(tmp_path: Path) -> None:
    watermark = tmp_path / "bronze" / "watermarks.json"
    watermark.parent.mkdir(parents=True)
    watermark.write_text('{"questions":"kept"}\n', encoding="utf-8")
    manifest, _bronze = publish_bronze(
        tmp_path,
        [
            answer(9, 2),
            question(
                2,
                Body="<p>Hello</p><pre><code>x = 1</code></pre>",
                Tags="<pandas><python><pandas>",
            ),
            question(1),
            answer(8, 1),
        ],
        (1, 2),
    )
    before = manifest.read_bytes()
    output = tmp_path / "silver" / "data_dump"
    result = transform_data_dump(manifest, output, transformation_run_id="pipeline")
    assert result["status"] == "completed"
    assert result["consumable"] is True
    assert manifest.read_bytes() == before
    assert watermark.read_text(encoding="utf-8") == '{"questions":"kept"}\n'
    assert not (tmp_path / "silver" / "questions").exists()
    assert not (tmp_path / "silver" / "answers").exists()
    generation = output / f"g={result['silver_generation_id']}"
    questions = _rows(generation / "question_content.jsonl")
    observations = _rows(generation / "question_observations.jsonl")
    answers = _rows(generation / "answer_observations.jsonl")
    assert [row["question_id"] for row in questions] == [1, 2]
    assert [row["source_row_number"] for row in observations] == [3, 2]
    assert [row["answer_id"] for row in answers] == [8, 9]
    assert [row["source_row_number"] for row in answers] == [4, 1]
    assert [row["is_accepted_in_snapshot"] for row in answers] == [True, False]
    coded = questions[1]
    assert coded["body_html"] == "<p>Hello</p><pre><code>x = 1</code></pre>"
    assert coded["body_text"] == "Hello"
    assert coded["code_blocks"] == ["x = 1"]
    assert coded["tags"] == ["pandas", "python"]
    assert coded["tags_raw"] == "<pandas><python><pandas>"
    quality = _rows(output / "quarantine" / f"g={result['silver_generation_id']}" / "items.jsonl")
    assert quality == [
        {
            "schema_version": "silver_data_dump_quality.v1",
            "code": "DS-014",
            "severity": "warning",
            "post_id": 2,
            "field": "accepted_answer_id",
            "line_effect": "kept",
            "run_effect": "completed",
        }
    ]
    _assert_portable(output, tmp_path)


def test_empty_bronze_publishes_consumable_empty_silver(tmp_path: Path) -> None:
    manifest, bronze = publish_bronze(tmp_path, [question(1)], (9,))
    assert bronze["status"] == "empty"
    output = tmp_path / "silver"
    result = transform_data_dump(manifest, output, transformation_run_id="empty-run")
    assert result["status"] == "empty"
    assert result["readable"] is True
    assert result["consumable"] is True
    generation = output / f"g={result['silver_generation_id']}"
    files = list(generation.glob("*.jsonl"))
    assert len(files) == 4
    assert all(path.read_bytes() == b"" for path in files)
    stored = json.loads(
        (output / "manifests" / f"{result['silver_generation_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["status"] == "empty"
    assert {item["records"] for item in stored["datasets"]} == {0}
    assert {item["sha256"] for item in stored["datasets"]} == {
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }


def test_partial_bronze_is_refused_before_any_silver_write(tmp_path: Path) -> None:
    manifest, bronze = publish_bronze(tmp_path, [question(1, Body="")], (1,))
    assert bronze["status"] == "partial"
    assert bronze["consumable"] is False
    output = tmp_path / "silver"
    result = transform_data_dump(manifest, output, transformation_run_id="refuse")
    assert result["failure_code"] == "DS-002"
    assert result["consumable"] is False
    assert not output.exists()


def test_replay_keeps_bytes_and_conflict_does_not_overwrite(tmp_path: Path) -> None:
    manifest, _bronze = publish_bronze(tmp_path, [question(1), answer(8, 1)], (1,))
    output = tmp_path / "silver"
    first = transform_data_dump(manifest, output, transformation_run_id="first")
    stored = output / "manifests" / f"{first['silver_generation_id']}.json"
    original = stored.read_bytes()
    content = output / f"g={first['silver_generation_id']}" / "question_content.jsonl"
    original_content = content.read_bytes()
    replay = transform_data_dump(manifest, output, transformation_run_id="second")
    assert replay["replay"] is True
    assert stored.read_bytes() == original
    assert content.read_bytes() == original_content
    content.write_bytes(original_content + b" ")
    conflict = transform_data_dump(manifest, output, transformation_run_id="third")
    assert conflict["failure_code"] == "DS-015"
    assert content.read_bytes() == original_content + b" "
    assert stored.read_bytes() == original


def test_checksum_schema_json_and_identity_failures_write_nothing(tmp_path: Path) -> None:
    manifest, _bronze = publish_bronze(tmp_path, [question(1)], (1,))
    path = shard_path(manifest)
    path.write_bytes(path.read_bytes().replace(b"title", b"Title", 1))
    output = tmp_path / "checksum"
    assert (
        transform_data_dump(manifest, output, transformation_run_id="checksum")["failure_code"]
        == "DS-004"
    )
    assert not output.exists()

    manifest, _bronze = publish_bronze(tmp_path / "schema", [question(1)], (1,))
    rewrite_shard(manifest, lambda rows: rows[0].__setitem__("schema_version", "other"))
    output = tmp_path / "schema-out"
    assert (
        transform_data_dump(manifest, output, transformation_run_id="schema")["failure_code"]
        == "DS-003"
    )
    assert not output.exists()

    manifest, _bronze = publish_bronze(tmp_path / "json", [question(1)], (1,))
    rewrite_raw(manifest, b"not-json\n")
    output = tmp_path / "json-out"
    assert (
        transform_data_dump(manifest, output, transformation_run_id="json")["failure_code"]
        == "DS-005"
    )
    assert not output.exists()

    manifest, _bronze = publish_bronze(tmp_path / "kind", [question(1)], (1,))
    rewrite_shard(manifest, lambda rows: rows[0].__setitem__("source_kind", "stackexchange_api"))
    output = tmp_path / "kind-out"
    assert (
        transform_data_dump(manifest, output, transformation_run_id="kind")["failure_code"]
        == "DS-006"
    )
    assert not output.exists()


def test_metric_and_content_changes_and_xml_order(tmp_path: Path) -> None:
    low, _bronze = publish_bronze(tmp_path / "low", [question(1, Score="3"), answer(8, 1)], (1,))
    high, _bronze = publish_bronze(tmp_path / "high", [question(1, Score="9"), answer(8, 1)], (1,))
    changed, _bronze = publish_bronze(
        tmp_path / "changed", [question(1, Body="Other body"), answer(8, 1)], (1,)
    )
    first_then_second, _bronze = publish_bronze(
        tmp_path / "forward", [question(1), question(2)], (1, 2)
    )
    second_then_first, _bronze = publish_bronze(
        tmp_path / "reverse", [question(2), question(1)], (1, 2)
    )
    low_out = _transform(low, tmp_path / "low-silver")
    high_out = _transform(high, tmp_path / "high-silver")
    changed_out = _transform(changed, tmp_path / "changed-silver")
    forward_out = _transform(first_then_second, tmp_path / "forward-silver")
    reverse_out = _transform(second_then_first, tmp_path / "reverse-silver")
    low_content = _rows(low_out / "question_content.jsonl")[0]
    high_content = _rows(high_out / "question_content.jsonl")[0]
    assert low_content["content_hash"] == high_content["content_hash"]
    assert _rows(low_out / "question_observations.jsonl")[0]["score"] == 3
    assert _rows(high_out / "question_observations.jsonl")[0]["score"] == 9
    assert low_out.name != high_out.name
    assert (
        _rows(changed_out / "question_content.jsonl")[0]["content_hash"]
        != low_content["content_hash"]
    )
    assert (forward_out / "question_content.jsonl").read_bytes() == (
        reverse_out / "question_content.jsonl"
    ).read_bytes()
    assert _rows(forward_out / "question_observations.jsonl")[0]["source_row_number"] == 1
    assert _rows(reverse_out / "question_observations.jsonl")[0]["source_row_number"] == 2


def test_missing_parent_acceptance_and_recoverable_rejections(tmp_path: Path) -> None:
    manifest, _bronze = publish_bronze(tmp_path / "orphan", [question(1), answer(8, 1)], (1,))
    rewrite_shard(manifest, lambda rows: rows.remove(rows[0]))
    output = tmp_path / "orphan-silver"
    orphan = transform_data_dump(manifest, output, transformation_run_id="orphan")
    assert orphan["status"] == "completed"
    assert orphan["consumable"] is True
    observations = _rows(
        output / f"g={orphan['silver_generation_id']}" / "answer_observations.jsonl"
    )
    assert observations[0]["is_accepted_in_snapshot"] is None
    quality = _rows(output / "quarantine" / f"g={orphan['silver_generation_id']}" / "items.jsonl")
    assert quality[0]["code"] == "DS-013"

    manifest, _bronze = publish_bronze(tmp_path / "absent", [question(1), answer(8, 1)], (1,))
    rewrite_shard(
        manifest,
        lambda rows: rows[0].__setitem__("accepted_answer_id", None),
    )
    absent = _transform(manifest, tmp_path / "absent-silver")
    assert _rows(absent / "answer_observations.jsonl")[0]["is_accepted_in_snapshot"] is False

    manifest, _bronze = publish_bronze(tmp_path / "parent", [question(1), answer(8, 1)], (1,))
    rewrite_shard(manifest, lambda rows: rows[1].__setitem__("parent_id", None))
    parent = transform_data_dump(
        manifest, tmp_path / "parent-silver", transformation_run_id="parent"
    )
    assert parent["status"] == "partial"
    assert parent["consumable"] is False
    assert parent["readable"] is True

    manifest, _bronze = publish_bronze(tmp_path / "type", [question(1)], (1,))
    rewrite_shard(manifest, lambda rows: rows[0].__setitem__("post_type_id", 3))
    assert (
        transform_data_dump(manifest, tmp_path / "type-silver", transformation_run_id="type")[
            "status"
        ]
        == "partial"
    )

    manifest, _bronze = publish_bronze(tmp_path / "time", [question(1)], (1,))
    rewrite_shard(manifest, lambda rows: rows[0].__setitem__("source_created_at", "yesterday"))
    timed = transform_data_dump(manifest, tmp_path / "time-silver", transformation_run_id="time")
    assert timed["status"] == "partial"

    manifest, _bronze = publish_bronze(tmp_path / "cutoff", [question(1)], (1,))
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["dump_cutoff"] = "2010-01-01T00:00:00Z"
    document["dump_cutoff_status"] = "operator_declared"
    manifest.write_bytes(canonical(document))
    cutoff = transform_data_dump(
        manifest, tmp_path / "cutoff-silver", transformation_run_id="cutoff"
    )
    assert cutoff["status"] == "partial"
    quality = _rows(
        tmp_path
        / "cutoff-silver"
        / "quarantine"
        / f"g={cutoff['silver_generation_id']}"
        / "items.jsonl"
    )
    assert quality[0]["code"] == "DS-011"


def test_conflicting_duplicate_aborts_and_identical_duplicate_collapses(tmp_path: Path) -> None:
    manifest, _bronze = publish_bronze(tmp_path / "conflict", [question(1)], (1,))
    rewrite_shard(manifest, lambda rows: rows.append({**rows[0], "score": 99}))
    output = tmp_path / "conflict-silver"
    assert (
        transform_data_dump(manifest, output, transformation_run_id="dup")["failure_code"]
        == "DS-009"
    )
    assert not output.exists()

    manifest, _bronze = publish_bronze(tmp_path / "same", [question(1)], (1,))
    rewrite_shard(manifest, lambda rows: rows.append(dict(rows[0])))
    result = transform_data_dump(manifest, tmp_path / "same-silver", transformation_run_id="same")
    assert result["status"] == "completed"
    assert result["counts"]["identical_duplicates"] == 1
    assert result["counts"]["question_content"] == 1


def test_lock_dry_run_body_limit_and_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _bronze = publish_bronze(tmp_path, [question(1), answer(8, 1)], (1,))
    output = tmp_path / "locked"
    (output / ".transform.lock").mkdir(parents=True)
    locked = transform_data_dump(manifest, output, transformation_run_id="locked")
    assert locked["failure_code"] == "DS-017"
    assert not (output / "manifests").exists()

    dry = tmp_path / "dry"
    preview = transform_data_dump(manifest, dry, transformation_run_id="dry", dry_run=True)
    assert preview["status"] == "dry_run"
    assert preview["planned_status"] == "completed"
    assert preview["destinations"]
    assert all(
        not str(item).startswith(("/", "\\")) and ":" not in item
        for item in preview["destinations"]
    )
    assert str(tmp_path) not in json.dumps(preview)
    assert not dry.exists()

    limited = transform_data_dump(
        manifest,
        tmp_path / "limited",
        transformation_run_id="limited",
        max_body_chars=1,
        dry_run=True,
    )
    assert limited["status"] == "partial"
    assert limited["planned_status"] == "partial"
    assert not (tmp_path / "limited").exists()

    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("disk")

    monkeypatch.setattr(
        "stackoverflow_vitality.data_dump_silver.transform.link_confirmed", fail_link
    )
    failed = transform_data_dump(manifest, tmp_path / "publish", transformation_run_id="publish")
    assert failed["failure_code"] == "DS-016"
    assert failed["consumable"] is False
    assert not (
        tmp_path / "publish" / "manifests" / f"{failed['silver_generation_id']}.json"
    ).exists()
    assert not (tmp_path / "publish" / ".transform.lock").exists()


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest, bronze = publish_bronze(tmp_path, [question(1)], (1,))
    output = tmp_path / "cli"
    code = main(
        [
            "transform-data-dump",
            "--bronze-generation",
            str(manifest),
            "--output-dir",
            str(output),
            "--transformation-run-id",
            "cli-run",
            "--dry-run",
        ]
    )
    assert code == 0
    assert str(tmp_path) not in capsys.readouterr().out
    assert not output.exists()
    assert (
        main(
            [
                "transform-data-dump",
                "--bronze-generation",
                str(manifest),
                "--output-dir",
                str(tmp_path / "cli-real"),
                "--transformation-run-id",
                "cli-real",
                "--max-body-chars",
                "1",
            ]
        )
        == 3
    )
    partial, _status = publish_bronze(tmp_path / "partial", [question(1, Body="")], (1,))
    assert bronze["consumable"] is True
    assert (
        main(
            [
                "transform-data-dump",
                "--bronze-generation",
                str(partial),
                "--output-dir",
                str(tmp_path / "refused"),
                "--transformation-run-id",
                "refused",
            ]
        )
        == 1
    )
    with pytest.raises(SystemExit) as caught:
        main(["transform-data-dump"])
    assert caught.value.code == 2


def test_missing_and_non_terminal_manifests_are_refused(tmp_path: Path) -> None:
    output = tmp_path / "missing"
    missing = transform_data_dump(tmp_path / "absent.json", output, transformation_run_id="missing")
    assert missing["failure_code"] == "DS-001"
    assert not output.exists()
    manifest, _bronze = publish_bronze(tmp_path / "pending", [question(1)], (1,))
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["published_at"] = None
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps(document), encoding="utf-8")
    refused = transform_data_dump(
        pending, tmp_path / "pending-out", transformation_run_id="pending"
    )
    assert refused["failure_code"] == "DS-001"
    assert not (tmp_path / "pending-out").exists()


def _transform(manifest: Path, output: Path) -> Path:
    result = transform_data_dump(manifest, output, transformation_run_id="run")
    assert result["status"] == "completed"
    return output / f"g={result['silver_generation_id']}"


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _assert_portable(output: Path, root: Path) -> None:
    for path in output.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert str(root) not in text
            assert "accepted_at" not in text
            assert "valid_from" not in text
