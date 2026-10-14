"""Gold v1 describes Silver question state without collapsing answer signals."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackoverflow_vitality.cli import main
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.gold.metrics import aggregate, latest_collected_at
from stackoverflow_vitality.gold.models import MONTHLY_FIELDS, TAG_FIELDS, nearest_rank
from stackoverflow_vitality.silver.history import empty_state, observe
from stackoverflow_vitality.silver.models import SilverQuestion, content_hash
from tests.silver_fixtures import COLLECTED, normalized, page, question, write_page

AS_OF = datetime(2024, 3, 1, tzinfo=UTC)
CREATED = datetime(2024, 1, 15, tzinfo=UTC)


class Questions:
    def __init__(self) -> None:
        self.state = empty_state()
        self._sequence = 0

    def add(self, **changes: object) -> None:
        self._sequence += 1
        record = to_record(normalized())
        values: dict[str, object] = {
            "question_id": self._sequence,
            "source_item_index": self._sequence,
            "source_file_checksum": f"{self._sequence:064x}",
            "collected_at": datetime(2024, 2, 1, tzinfo=UTC),
            "creation_at": CREATED,
        }
        values.update(changes)
        for key, value in values.items():
            if isinstance(value, datetime):
                record[key] = value.isoformat().replace("+00:00", "Z")
            elif isinstance(value, tuple):
                record[key] = list(value)
            else:
                record[key] = value
        record["record_hash"] = content_hash(record)
        assert observe(self.state, SilverQuestion.from_record(record))


def test_nearest_rank_uses_ceil_on_sorted_known_values() -> None:
    assert nearest_rank([1, 2, 3, 4, 5], 500_000) == 3
    assert nearest_rank([1, 2, 3, 4, 5], 900_000) == 5
    assert nearest_rank([1, 2], 500_000) == 1
    assert nearest_rank([7], 900_000) == 7


def test_null_metrics_stay_unknown_and_absent_accepted_id_is_false() -> None:
    questions = Questions()
    questions.add(
        score=None,
        view_count=None,
        answer_count=None,
        is_answered=None,
        accepted_answer_id=None,
        tags=None,
    )
    row = aggregate(questions.state, AS_OF).monthly[0]
    assert row["score_sum"] is None and row["score_known"] == 0 and row["score_unknown"] == 1
    assert row["view_count_sum"] is None and row["view_count_unknown"] == 1
    assert row["answer_count_sum"] is None and row["answer_count_known"] == 0
    assert row["has_answers_unknown"] == 1
    assert row["has_answers_true"] == 0 and row["has_answers_false"] == 0
    assert row["source_is_answered_unknown"] == 1 and row["source_is_answered_false"] == 0
    assert row["has_accepted_answer_false"] == 1 and row["has_accepted_answer_true"] == 0
    assert row["is_answered_false_with_answers"] == 0
    assert row["is_answered_true_without_answers"] == 0
    assert row["accepted_without_answers"] == 0
    assert row["questions_missing_tags"] == 1
    assert row["max_tag_question_share_ppm"] is None
    assert row["tag_assignment_hhi_ppm"] is None
    assert row["closed_unknown"] == 1 and row["closed_known"] == 0


def test_known_answer_disagreements_are_counted_without_being_reconciled() -> None:
    questions = Questions()
    questions.add(question_id=1, is_answered=False, answer_count=3, accepted_answer_id=None)
    questions.add(question_id=2, is_answered=True, answer_count=0, accepted_answer_id=None)
    questions.add(question_id=3, is_answered=False, answer_count=0, accepted_answer_id=8)
    questions.add(question_id=4, is_answered=None, answer_count=None, accepted_answer_id=9)
    row = aggregate(questions.state, AS_OF).monthly[0]
    assert row["questions"] == 4
    assert row["source_is_answered_false"] == 2
    assert row["source_is_answered_true"] == 1
    assert row["source_is_answered_unknown"] == 1
    assert row["has_answers_true"] == 1
    assert row["has_answers_false"] == 2
    assert row["has_answers_unknown"] == 1
    assert row["has_accepted_answer_true"] == 2
    assert row["has_accepted_answer_false"] == 2
    assert row["is_answered_false_with_answers"] == 1
    assert row["is_answered_true_without_answers"] == 1
    assert row["accepted_without_answers"] == 1
    assert row["answer_count_sum"] == 3


def test_cutoff_reads_observations_and_content_known_at_as_of() -> None:
    questions = Questions()
    questions.add(
        question_id=7,
        score=1,
        view_count=4,
        answer_count=0,
        is_answered=False,
        accepted_answer_id=None,
        tags=("python",),
        closed_at=None,
        collected_at=datetime(2024, 2, 1, tzinfo=UTC),
    )
    questions.add(
        question_id=7,
        score=9,
        view_count=40,
        answer_count=2,
        is_answered=True,
        accepted_answer_id=3,
        tags=("python", "sql"),
        closed_at=datetime(2024, 3, 20, tzinfo=UTC),
        collected_at=datetime(2024, 4, 1, tzinfo=UTC),
    )
    assert questions.state["questions"] == []
    early = aggregate(questions.state, AS_OF)
    row = early.monthly[0]
    assert early.questions_included == 1 and row["questions"] == 1
    assert row["score_max"] == 1 and row["view_count_max"] == 4
    assert row["has_answers_false"] == 1 and row["has_answers_true"] == 0
    assert row["source_is_answered_false"] == 1
    assert row["has_accepted_answer_false"] == 1
    assert row["closed_unknown"] == 1 and row["closed_known"] == 0
    assert [item["tag"] for item in early.tag_monthly] == ["python"]
    late = aggregate(questions.state, datetime(2024, 4, 1, tzinfo=UTC))
    current = late.monthly[0]
    assert current["score_max"] == 9 and current["has_answers_true"] == 1
    assert current["source_is_answered_true"] == 1
    assert current["has_accepted_answer_true"] == 1 and current["closed_known"] == 1
    assert [item["tag"] for item in late.tag_monthly] == ["python", "sql"]
    before = aggregate(questions.state, datetime(2024, 4, 1, tzinfo=UTC) - timedelta(seconds=1))
    assert before.monthly[0]["score_max"] == 1
    assert before.monthly[0]["closed_known"] == 0
    assert [item["tag"] for item in before.tag_monthly] == ["python"]


def test_same_content_interval_keeps_metrics_at_or_before_the_cutoff() -> None:
    questions = Questions()
    questions.add(
        question_id=7,
        score=1,
        answer_count=0,
        is_answered=False,
        tags=("python",),
        collected_at=datetime(2024, 2, 1, tzinfo=UTC),
    )
    questions.add(
        question_id=7,
        score=9,
        answer_count=4,
        is_answered=True,
        tags=("python",),
        collected_at=datetime(2024, 4, 1, tzinfo=UTC),
    )
    early = aggregate(questions.state, datetime(2024, 3, 1, tzinfo=UTC))
    assert early.monthly[0]["score_max"] == 1
    assert early.monthly[0]["answer_count_sum"] == 0
    assert [item["tag"] for item in early.tag_monthly] == ["python"]
    assert questions.state["questions"][0]["score"] == 9
    at_later = aggregate(questions.state, datetime(2024, 4, 1, tzinfo=UTC))
    assert at_later.monthly[0]["score_max"] == 9
    assert at_later.monthly[0]["answer_count_sum"] == 4


def test_tied_observations_follow_the_silver_content_winner() -> None:
    questions = Questions()
    instant = datetime(2024, 2, 1, tzinfo=UTC)
    questions.add(question_id=7, score=1, tags=("python",), collected_at=instant)
    questions.add(question_id=7, score=9, tags=("sql",), collected_at=instant)
    observations = list(questions.state["observations"].values())
    winner = max(
        observations,
        key=lambda item: (item["collected_at"], item["record_hash"], item["observation_id"]),
    )
    result = aggregate(questions.state, AS_OF)
    content = questions.state["catalog"][winner["record_hash"]]
    assert result.monthly[0]["score_max"] == winner["score"]
    assert [item["tag"] for item in result.tag_monthly] == list(content["tags"])


def test_derived_cutoff_is_not_an_aggregate_default() -> None:
    questions = Questions()
    questions.add(question_id=7, score=1, collected_at=datetime(2024, 2, 1, tzinfo=UTC))
    questions.add(question_id=7, score=9, collected_at=datetime(2024, 4, 1, tzinfo=UTC))
    assert latest_collected_at(questions.state) == datetime(2024, 4, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="UTC"):
        aggregate(questions.state, datetime(2024, 4, 1))


def test_questions_outside_the_cutoff_are_excluded() -> None:
    questions = Questions()
    questions.add(
        question_id=1,
        creation_at=datetime(2024, 2, 15, tzinfo=UTC),
        collected_at=datetime(2024, 2, 1, tzinfo=UTC),
    )
    questions.add(
        question_id=2,
        creation_at=datetime(2024, 1, 1, tzinfo=UTC),
        collected_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    result = aggregate(questions.state, datetime(2024, 2, 10, tzinfo=UTC))
    assert result.monthly == () and result.tag_monthly == ()
    assert result.questions_included == 0
    assert result.questions_created_after_as_of == 1
    assert result.questions_without_observation == 1


def test_month_comes_from_creation_and_tags_are_not_a_second_population() -> None:
    questions = Questions()
    questions.add(
        question_id=1,
        tags=("python", "sql", "testing"),
        score=-2,
        view_count=0,
        answer_count=1,
        is_answered=True,
        accepted_answer_id=None,
    )
    questions.add(question_id=2, site="superuser", tags=("python",), score=4, answer_count=0)
    result = aggregate(questions.state, AS_OF)
    assert result.questions_included == 2
    monthly = {row["site"]: row for row in result.monthly}
    primary = monthly["stackoverflow"]
    assert primary["creation_month"] == "2024-01"
    assert primary["questions"] == 1 and primary["tag_assignments"] == 3
    assert primary["distinct_tags"] == 3 and primary["max_tag_questions"] == 1
    assert primary["max_tag_question_share_ppm"] == 1_000_000
    assert primary["tag_assignment_hhi_ppm"] == 333_333
    assert primary["score_min"] == -2 and primary["score_sum"] == -2
    assert primary["view_count_min"] == 0 and primary["view_count_sum"] == 0
    exposure = (AS_OF - CREATED).days * 86400 + (AS_OF - CREATED).seconds
    assert primary["exposure_seconds_min"] == primary["exposure_seconds_p50"] == exposure
    assert primary["exposure_seconds_max"] == exposure
    shares = [
        item["assignment_share_ppm"]
        for item in result.tag_monthly
        if item["site"] == "stackoverflow"
    ]
    assert shares == [333_333, 333_333, 333_333]
    assert sum(shares) == 999_999
    assert monthly["superuser"]["questions"] == 1
    assert set(primary) == set(MONTHLY_FIELDS)
    assert len(MONTHLY_FIELDS) == len(set(MONTHLY_FIELDS))
    assert all(set(item) == set(TAG_FIELDS) for item in result.tag_monthly)
    again = aggregate(questions.state, AS_OF)
    assert again.monthly == result.monthly and again.tag_monthly == result.tag_monthly


def test_empty_observations_require_an_explicit_cutoff() -> None:
    with pytest.raises(ValueError, match="as_of is required"):
        latest_collected_at(empty_state())
    result = aggregate(empty_state(), AS_OF)
    assert result.monthly == () and result.questions_included == 0


def test_cli_publishes_a_deterministic_descriptive_generation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_page(
        tmp_path / "bronze",
        page([question(), question(question_id=456, is_answered=True, answer_count=0)]),
    )
    assert _transform(tmp_path) == 0
    capsys.readouterr()
    gold = tmp_path / "gold"
    cutoff = COLLECTED.isoformat().replace("+00:00", "Z")
    args = [
        "build-vitality",
        "--silver-path",
        str(tmp_path / "silver"),
        "--gold-path",
        str(gold),
        "--as-of",
        cutoff,
    ]
    assert main([*args[:5]]) == 1
    assert "as_of is required" in capsys.readouterr().err
    assert not gold.exists()
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "completed" and first["descriptive_only"] is True
    assert first["causal_claims"] == "unsupported"
    assert first["as_of"] == cutoff
    assert first["as_of_source"] == "explicit"
    assert first["questions_included"] == 2
    monthly_path = Path(first["output_paths"]["monthly"])
    manifest_path = Path(first["manifest_path"])
    monthly_bytes = monthly_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["causal_claims"] == "unsupported" and manifest["descriptive_only"] is True
    assert manifest["as_of_source"] == "explicit"
    assert manifest["silver_accepted_records"] == 2
    assert manifest["silver_quarantined_records"] == 0
    assert manifest["silver_quality_warnings"] == 1
    row = json.loads(monthly_bytes.splitlines()[0])
    created = datetime.fromtimestamp(1788220800, UTC)
    assert row["creation_month"] == f"{created.year:04d}-{created.month:02d}"
    assert row["questions"] == 2
    assert row["source_is_answered_true"] == 2
    assert row["has_answers_true"] == 1 and row["has_answers_false"] == 1
    assert row["is_answered_true_without_answers"] == 1
    assert row["is_answered_false_with_answers"] == 0
    assert row["has_accepted_answer_false"] == 2
    assert row["max_tag_question_share_ppm"] == 1_000_000
    tags = [
        json.loads(line)
        for line in Path(first["output_paths"]["tag_monthly"]).read_bytes().splitlines()
    ]
    assert [item["tag"] for item in tags] == ["python", "testing"]
    assert [item["questions"] for item in tags] == [2, 2]
    assert [item["assignment_share_ppm"] for item in tags] == [500_000, 500_000]
    assert main(args) == 0
    capsys.readouterr()
    assert monthly_path.read_bytes() == monthly_bytes
    assert manifest_path.read_bytes() == manifest_bytes
    assert main([*args[:-2], "--as-of", "2020-01-01T00:00:00Z"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["as_of_source"] == "explicit"
    assert second["questions_included"] == 0
    assert second["questions_without_observation"] == 2
    assert second["publication_id"] != first["publication_id"]
    assert monthly_path.read_bytes() == monthly_bytes
    assert Path(second["manifest_path"]).read_bytes() != manifest_bytes


def test_dry_run_and_rejected_arguments_do_not_publish(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_page(tmp_path / "bronze")
    assert _transform(tmp_path) == 0
    capsys.readouterr()
    gold = tmp_path / "gold"
    base = ["build-vitality", "--silver-path", str(tmp_path / "silver"), "--gold-path", str(gold)]
    assert main([*base, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["status"] == "dry_run" and dry["manifest_path"] is None
    assert dry["as_of_source"] == "derived_max_collected_at"
    assert dry["as_of"] == COLLECTED.isoformat().replace("+00:00", "Z")
    assert dry["causal_claims"] == "unsupported"
    assert not gold.exists()
    assert main([*base, "--dry-run", "--as-of", "2020-01-01T00:00:00Z"]) == 0
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["status"] == "dry_run" and explicit["as_of_source"] == "explicit"
    assert explicit["as_of"] == "2020-01-01T00:00:00Z"
    assert explicit["questions_included"] == 0
    assert not gold.exists()
    with pytest.raises(SystemExit) as failure:
        main([*base, "--as-of", "2024-01-01"])
    assert failure.value.code == 2
    assert not gold.exists()
    with pytest.raises(SystemExit) as offset:
        main([*base, "--as-of", "2024-01-01T00:00:00+00:00"])
    assert offset.value.code == 2
    assert not gold.exists()
    with pytest.raises(SystemExit) as calendar:
        main([*base, "--as-of", "2024-13-01T00:00:00Z"])
    assert calendar.value.code == 2
    assert not gold.exists()
    assert (
        main(
            [
                "build-vitality",
                "--silver-path",
                str(tmp_path / "silver"),
                "--gold-path",
                str(tmp_path / "silver"),
            ]
        )
        == 1
    )
    assert "must not overlap" in capsys.readouterr().err
    assert (
        main(
            ["build-vitality", "--silver-path", str(tmp_path / "missing"), "--gold-path", str(gold)]
        )
        == 1
    )
    assert "existing directory" in capsys.readouterr().err


def test_empty_silver_can_publish_only_with_an_explicit_cutoff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bronze").mkdir()
    assert _transform(tmp_path) == 0
    capsys.readouterr()
    args = [
        "build-vitality",
        "--silver-path",
        str(tmp_path / "silver"),
        "--gold-path",
        str(tmp_path / "gold"),
    ]
    assert main(args) == 1
    assert "as_of is required" in capsys.readouterr().err
    assert main([*args, "--as-of", "2024-03-01T00:00:00Z"]) == 0
    published = json.loads(capsys.readouterr().out)
    assert published["questions_included"] == 0
    assert Path(published["output_paths"]["monthly"]).read_bytes() == b""
    assert Path(published["output_paths"]["tag_monthly"]).read_bytes() == b""


def _transform(tmp_path: Path) -> int:
    return main(
        [
            "transform-questions",
            "--bronze-path",
            str(tmp_path / "bronze"),
            "--silver-path",
            str(tmp_path / "silver"),
            "--quarantine-path",
            str(tmp_path / "quarantine"),
        ]
    )
