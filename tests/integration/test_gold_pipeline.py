"""Bronze to Silver to Gold: cutoff, content version, and immutable publication."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackoverflow_vitality.cli import main
from stackoverflow_vitality.silver.storage import (
    LocalBronzeReader,
    LocalQuarantineStore,
    LocalSilverStore,
    LocalTransformationManifestStore,
)
from stackoverflow_vitality.silver.transform import transform_questions
from tests.silver_fixtures import page, question, write_page

EARLY = datetime(2026, 9, 10, tzinfo=UTC)
LATE = datetime(2026, 10, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 14, tzinfo=UTC)
MID = "2026-09-20T00:00:00Z"
LATE_TEXT = "2026-10-01T00:00:00Z"


def test_pipeline_separates_content_version_from_metric_observation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _publish_silver(tmp_path)
    silver_manifest = next((tmp_path / "silver" / "manifests").glob("*.json")).read_bytes()
    base = [
        "build-vitality",
        "--silver-path",
        str(tmp_path / "silver"),
        "--gold-path",
        str(tmp_path / "gold"),
    ]
    assert main(base) == 1
    assert "as_of is required" in capsys.readouterr().err
    assert not (tmp_path / "gold").exists()
    assert main([*base, "--dry-run"]) == 0
    derived = json.loads(capsys.readouterr().out)
    assert derived["status"] == "dry_run"
    assert derived["as_of_source"] == "derived_max_collected_at"
    assert derived["as_of"] == LATE_TEXT
    assert derived["questions_included"] == 1
    assert not (tmp_path / "gold").exists()
    assert main([*base, "--dry-run", "--as-of", MID]) == 0
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["as_of_source"] == "explicit" and explicit["as_of"] == MID
    assert not (tmp_path / "gold").exists()

    assert main([*base, "--as-of", MID]) == 0
    mid = json.loads(capsys.readouterr().out)
    assert mid["as_of_source"] == "explicit"
    mid_row = _rows(mid["output_paths"]["monthly"])[0]
    assert mid_row["score_max"] == 1
    assert mid_row["has_answers_false"] == 1 and mid_row["source_is_answered_false"] == 1
    assert mid_row["closed_known"] == 0 and mid_row["questions"] == 1
    assert [item["tag"] for item in _rows(mid["output_paths"]["tag_monthly"])] == ["python"]
    first_bytes = Path(mid["output_paths"]["monthly"]).read_bytes()

    assert main([*base, "--as-of", LATE_TEXT]) == 0
    late = json.loads(capsys.readouterr().out)
    late_row = _rows(late["output_paths"]["monthly"])[0]
    late_tags = _rows(late["output_paths"]["tag_monthly"])
    assert late_row["questions"] == 1 and late_row["tag_assignments"] == 2
    assert late_row["score_max"] == 9 and late_row["closed_known"] == 1
    assert late_row["has_answers_true"] == 1
    assert [item["tag"] for item in late_tags] == ["python", "sql"]
    assert sum(item["questions"] for item in late_tags) == 2
    assert Path(mid["output_paths"]["monthly"]).read_bytes() == first_bytes
    late_bytes = Path(late["output_paths"]["monthly"]).read_bytes()
    manifest = json.loads(Path(late["manifest_path"]).read_bytes())
    assert manifest["as_of_source"] == "explicit"
    assert manifest["silver_accepted_records"] == 2
    assert manifest["descriptive_only"] is True and manifest["causal_claims"] == "unsupported"

    assert main([*base, "--as-of", LATE_TEXT]) == 0
    capsys.readouterr()
    assert Path(late["output_paths"]["monthly"]).read_bytes() == late_bytes
    Path(late["output_paths"]["monthly"]).write_bytes(b'{"diverged":true}\n')
    assert main([*base, "--as-of", LATE_TEXT]) == 1
    assert "differs" in capsys.readouterr().err
    assert Path(late["output_paths"]["monthly"]).read_bytes() == b'{"diverged":true}\n'
    assert next((tmp_path / "silver" / "manifests").glob("*.json")).read_bytes() == silver_manifest


def test_existing_lock_blocks_publication_without_a_generation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _publish_silver(tmp_path)
    gold = tmp_path / "gold"
    (gold / ".gold.lock").mkdir(parents=True)
    code = main(
        [
            "build-vitality",
            "--silver-path",
            str(tmp_path / "silver"),
            "--gold-path",
            str(gold),
            "--as-of",
            LATE_TEXT,
        ]
    )
    assert code == 1
    assert "lock" in capsys.readouterr().err
    assert not (gold / "manifests").exists()
    assert not (gold / "vitality").exists()


def _publish_silver(tmp_path: Path) -> None:
    bronze = tmp_path / "bronze"
    write_page(
        bronze,
        page(
            [question(score=1, tags=["python"], answer_count=0, is_answered=False)],
            collected=EARLY,
            number=1,
        ),
    )
    write_page(
        bronze,
        page(
            [
                question(
                    score=9,
                    tags=["python", "sql"],
                    answer_count=2,
                    is_answered=True,
                    closed_date=int(datetime(2026, 9, 20, tzinfo=UTC).timestamp()),
                )
            ],
            collected=LATE,
            number=2,
        ),
    )
    result = transform_questions(
        LocalBronzeReader(bronze),
        LocalSilverStore(tmp_path / "silver"),
        LocalQuarantineStore(tmp_path / "quarantine"),
        LocalTransformationManifestStore(tmp_path / "silver" / "manifests"),
        now=lambda: NOW,
        monotonic=lambda: 1.0,
    )
    assert result.status == "completed"
    assert result.accepted_records == 2 and result.quarantined_records == 0


def _rows(path: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in Path(path).read_bytes().splitlines()]
