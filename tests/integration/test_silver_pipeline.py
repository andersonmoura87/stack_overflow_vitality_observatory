"""Local synthetic Bronze -> quality/history -> Silver -> quarantine -> commit."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from stackoverflow_vitality.cli import main
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.silver import storage
from stackoverflow_vitality.silver.models import digest, validate_history
from stackoverflow_vitality.silver.storage import (
    LocalBronzeReader,
    LocalQuarantineStore,
    LocalSilverStore,
    LocalTransformationManifestStore,
    validate_paths,
)
from stackoverflow_vitality.silver.transform import transform_questions
from stackoverflow_vitality.storage.files import canonical, exclusive_lock
from tests.silver_fixtures import (
    COLLECTED,
    NOW,
    SOURCE_ID,
    TRANSFORM_ID,
    page,
    question,
    write_page,
)


def execute(
    tmp_path, *, run_id=TRANSFORM_ID, silver=None, quarantine=None, manifests=None, **kwargs
):
    silver = silver or LocalSilverStore(tmp_path / "silver")
    quarantine = quarantine or LocalQuarantineStore(tmp_path / "quarantine")
    manifests = manifests or LocalTransformationManifestStore(tmp_path / "silver/manifests")
    result = transform_questions(
        LocalBronzeReader(tmp_path / "bronze"),
        silver,
        quarantine,
        manifests,
        transformation_run_id=run_id,
        now=lambda: NOW,
        monotonic=lambda: 1.0,
        **kwargs,
    )
    return result, silver, manifests


def rows(path):
    return [json.loads(line) for line in Path(path).read_bytes().splitlines()]


def test_complete_pipeline_and_manifest_with_quarantine(tmp_path, caplog):
    secret = "SYNTHETIC_BODY_NEVER_IN_LOGS"
    snapshot = page(
        [
            question(),
            question(question_id=None, body=secret),
            question(question_id=456, is_answered=True, answer_count=0),
        ]
    )
    bronze_path = write_page(tmp_path / "bronze", snapshot)
    original = bronze_path.read_bytes()
    result, silver, manifests = execute(tmp_path)
    assert result.status == "completed"
    assert result.input_records == 3 and result.accepted_records == 2
    assert result.quarantined_records == 1
    assert result.quality_errors == 1 and result.quality_warnings == 1
    assert result.new_versions == 2 and result.closed_versions == 0
    assert len(rows(result.output_paths["quarantined_records"])) == 1
    assert len(rows(result.output_paths["quality_events"])) == 2
    assert secret not in caplog.text
    assert secret not in Path(result.output_paths["quarantined_records"]).read_text()
    state = silver.load(manifests.latest(silver.dataset_id))
    assert len(state["questions"]) == 2
    validate_history(state["questions"])
    for name, path in result.output_paths.items():
        assert digest(Path(path).read_bytes()) == result.output_checksums[name]
    assert next(iter(result.input_checksums.values())) == digest(original)
    assert bronze_path.read_bytes() == original
    manifest_bytes = (tmp_path / "silver/manifests" / f"{TRANSFORM_ID}.json").read_bytes()
    assert manifest_bytes == canonical(to_record(result))
    assert canonical(to_record(result)) == canonical(to_record(replace(result)))


def test_reprocess_keeps_generation_bytes_and_counts_unchanged(tmp_path):
    write_page(tmp_path / "bronze")
    first, _, _ = execute(tmp_path)
    before = {
        key: Path(first.output_paths[key]).stat().st_mtime_ns
        for key in ("catalog", "observations", "questions")
    }
    second, silver, manifests = execute(tmp_path, run_id="00000000-0000-0000-0000-000000000100")
    assert second.status == "completed"
    assert second.unchanged_records == 1 and second.new_versions == 0
    assert second.commit_sequence == 2
    for key in before:
        assert first.output_paths[key] == second.output_paths[key]
        assert Path(second.output_paths[key]).stat().st_mtime_ns == before[key]
    assert len(silver.load(manifests.latest(silver.dataset_id))["observations"]) == 1


def test_later_content_closes_version_and_late_snapshot_rebuilds(tmp_path):
    write_page(tmp_path / "bronze", page(collected=COLLECTED + timedelta(days=2)))
    first, _, _ = execute(tmp_path)
    assert first.new_versions == 1
    second_id = "00000000-0000-0000-0000-000000000002"
    write_page(
        tmp_path / "bronze",
        page([question(body="<p>Earlier</p>")], run_id=second_id, collected=COLLECTED),
    )
    result, silver, manifests = execute(
        tmp_path, source_run_id=second_id, run_id="00000000-0000-0000-0000-000000000100"
    )
    assert result.status == "completed" and result.new_versions == 1
    state = silver.load(manifests.latest(silver.dataset_id))
    assert len(state["questions"]) == 2
    assert state["questions"][0]["valid_to"] == state["questions"][1]["valid_from"]
    assert state["questions"][1]["is_current"]


@pytest.mark.parametrize(
    "boundary", ["silver_write", "quarantine_write", "output_confirmation", "manifest_terminal"]
)
def test_partial_failure_never_commits_success(tmp_path, boundary):
    write_page(tmp_path / "bronze")

    class BrokenSilver(LocalSilverStore):
        confirmations = 0

        def write(self, state):
            if boundary == "silver_write":
                raise OSError("secret value")
            return super().write(state)

        def confirm(self, paths, expected):
            self.confirmations += 1
            if boundary == "output_confirmation" and self.confirmations == 2:
                raise OSError("secret value")
            super().confirm(paths, expected)

    class BrokenQuarantine(LocalQuarantineStore):
        def write(self, *args):
            if boundary == "quarantine_write":
                raise OSError("secret value")
            return super().write(*args)

    class BrokenManifest(LocalTransformationManifestStore):
        def write(self, value):
            if boundary == "manifest_terminal" and value.status == "completed":
                raise OSError("secret value")
            return super().write(value)

    result, silver, manifests = execute(
        tmp_path,
        silver=BrokenSilver(tmp_path / "silver"),
        quarantine=BrokenQuarantine(tmp_path / "quarantine"),
        manifests=BrokenManifest(tmp_path / "silver/manifests"),
    )
    assert result.status == "failed"
    assert result.failure_stage == boundary
    assert manifests.latest(silver.dataset_id) is None
    terminal = json.loads((tmp_path / "silver/manifests" / f"{TRANSFORM_ID}.json").read_bytes())
    assert terminal["status"] == "failed" and terminal["error_type"] == "OSError"
    assert "secret value" not in json.dumps(terminal)


def test_expected_checksums_detect_valid_json_tampering(tmp_path):
    write_page(tmp_path / "bronze")

    class Tampered(LocalSilverStore):
        def write(self, state):
            paths, expected = super().write(state)
            Path(paths["questions"]).write_bytes(b"{}\n")
            return paths, expected

    result, silver, manifests = execute(tmp_path, silver=Tampered(tmp_path / "silver"))
    assert result.status == "failed"
    assert manifests.latest(silver.dataset_id) is None


def test_corrupt_bronze_fails_without_replacing_previous_generation(tmp_path):
    path = write_page(tmp_path / "bronze")
    first, silver, manifests = execute(tmp_path)
    envelope = json.loads(path.read_bytes())
    envelope["payload"]["items"][0]["title"] = "tampered"
    path.write_bytes(canonical(envelope))
    second, _, _ = execute(tmp_path, run_id="00000000-0000-0000-0000-000000000100")
    assert second.status == "failed"
    assert (
        manifests.latest(silver.dataset_id)["transformation_run_id"] == first.transformation_run_id
    )


def test_missing_lineage_goes_to_quarantine_not_silent_drop(tmp_path):
    path = write_page(tmp_path / "bronze")
    envelope = json.loads(path.read_bytes())
    del envelope["recovered_at"]
    path.write_bytes(canonical(envelope))
    result, _, _ = execute(tmp_path)
    assert result.status == "completed"
    assert result.accepted_records == 0 and result.quarantined_records == 1
    assert rows(result.output_paths["quarantined_records"])[0]["rule_id"] == "SQ-003"


def test_nonobject_item_is_quarantined(tmp_path):
    write_page(tmp_path / "bronze", page([None]))
    result, _, _ = execute(tmp_path)
    assert result.quarantined_records == 1 and result.quality_errors == 1


def test_empty_input_has_manifest(tmp_path):
    (tmp_path / "bronze").mkdir()
    result, _, _ = execute(tmp_path)
    assert result.status == "empty" and result.input_records == 0


def test_lock_conflict_is_failure_and_no_commit(tmp_path):
    write_page(tmp_path / "bronze")
    with exclusive_lock(tmp_path / "silver/.transform.lock"):
        result, silver, manifests = execute(tmp_path)
        assert result.status == "failed" and result.error_type == "LocalLockConflict"
        assert manifests.latest(silver.dataset_id) is None


def test_atomic_failure_preserves_previous_state(tmp_path, monkeypatch):
    write_page(tmp_path / "bronze")
    first, silver, manifests = execute(tmp_path)
    write_page(
        tmp_path / "bronze",
        page([question(title="Changed")], number=2, collected=COLLECTED + timedelta(days=1)),
    )

    def fail(*args):
        raise OSError("sync failed")

    monkeypatch.setattr(storage.os, "fsync", fail)
    result, _, _ = execute(tmp_path, run_id="00000000-0000-0000-0000-000000000100")
    assert result.status == "failed"
    assert (
        manifests.latest(silver.dataset_id)["transformation_run_id"] == first.transformation_run_id
    )
    assert not list((tmp_path / "silver").rglob(".pending-*"))


def cli_args(tmp_path):
    return [
        "transform-questions",
        "--bronze-path",
        str(tmp_path / "bronze"),
        "--silver-path",
        str(tmp_path / "silver"),
        "--quarantine-path",
        str(tmp_path / "quarantine"),
        "--run-id",
        SOURCE_ID,
    ]


def test_cli_dry_run_no_outputs_then_transform(tmp_path, capsys):
    write_page(tmp_path / "bronze")
    assert main(cli_args(tmp_path) + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    assert not (tmp_path / "silver").exists()
    assert not (tmp_path / "quarantine").exists()
    assert main(cli_args(tmp_path)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["accepted_records"] == 1
    assert "body_html" not in result


def test_cli_missing_path_and_invalid_limit_fail_before_processing(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(cli_args(tmp_path))
    assert error.value.code == 2
    write_page(tmp_path / "bronze")
    with pytest.raises(SystemExit) as error:
        main(cli_args(tmp_path) + ["--max-body-chars", "0"])
    assert error.value.code == 2
    assert not (tmp_path / "silver").exists()


def test_cli_failure_exit_code(tmp_path, capsys):
    path = write_page(tmp_path / "bronze")
    path.write_text("{", encoding="utf-8")
    assert main(cli_args(tmp_path)) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_output_paths_cannot_overlap_bronze(tmp_path):
    (tmp_path / "bronze").mkdir()
    with pytest.raises(ValueError):
        validate_paths(tmp_path / "bronze", tmp_path / "bronze/silver", tmp_path / "quarantine")


def test_run_filter_does_not_transform_other_sources(tmp_path):
    write_page(tmp_path / "bronze")
    other = "00000000-0000-0000-0000-000000000002"
    write_page(tmp_path / "bronze", page([question(question_id=456)], run_id=other))
    result, _, _ = execute(tmp_path, source_run_id=SOURCE_ID)
    assert result.input_records == 1 and result.source_run_ids == (SOURCE_ID,)
