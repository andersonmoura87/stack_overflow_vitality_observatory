"""Gold publication rejects records that drift from the versioned contract."""

from datetime import UTC, datetime

import pytest

from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.gold.metrics import aggregate
from stackoverflow_vitality.gold.models import (
    CAUSAL_CLAIMS,
    LIMITATION,
    MONTHLY_FIELDS,
    MONTHLY_SCHEMA,
    PUBLICATION_FIELDS,
    PUBLICATION_SCHEMA,
    TAG_FIELDS,
    TAG_SCHEMA,
    TRANSFORMER_VERSION,
    hhi_ppm,
    nearest_rank,
    share_ppm,
    stamp,
    validate_monthly,
    validate_publication,
    validate_tag,
)
from stackoverflow_vitality.silver.history import empty_state, observe
from stackoverflow_vitality.silver.models import SilverQuestion, content_hash
from tests.silver_fixtures import normalized

AS_OF = datetime(2024, 3, 1, tzinfo=UTC)


def _row() -> dict[str, object]:
    state = empty_state()
    record = to_record(normalized())
    record["collected_at"] = "2024-02-01T00:00:00Z"
    record["creation_at"] = "2024-01-15T00:00:00Z"
    record["record_hash"] = content_hash(record)
    assert observe(state, SilverQuestion.from_record(record))
    return aggregate(state, AS_OF).monthly[0]


def _publication() -> dict[str, object]:
    return {
        "schema_version": PUBLICATION_SCHEMA,
        "transformer_version": TRANSFORMER_VERSION,
        "silver_schema_version": "silver_questions.v1",
        "silver_dataset_id": "a" * 64,
        "silver_commit_sequence": 1,
        "silver_transformation_run_id": "00000000-0000-0000-0000-000000000099",
        "silver_input_records": 1,
        "silver_accepted_records": 1,
        "silver_quarantined_records": 0,
        "silver_quality_errors": 0,
        "silver_quality_warnings": 0,
        "as_of": "2024-03-01T00:00:00Z",
        "as_of_source": "explicit",
        "questions_included": 1,
        "questions_without_observation": 0,
        "questions_created_after_as_of": 0,
        "monthly_rows": 1,
        "tag_rows": 1,
        "output_checksums": {"monthly": "b" * 64, "tag_monthly": "c" * 64},
        "descriptive_only": True,
        "causal_claims": CAUSAL_CLAIMS,
        "limitation": LIMITATION,
        "publication_id": "d" * 64,
        "output_paths": {"monthly": "monthly.jsonl", "tag_monthly": "tag_monthly.jsonl"},
    }


def test_published_rows_use_the_contract_field_sets() -> None:
    row = _row()
    assert set(row) == set(MONTHLY_FIELDS)
    assert row["schema_version"] == MONTHLY_SCHEMA
    validate_monthly(row)
    tag = aggregate_tag()
    assert set(tag) == set(TAG_FIELDS)
    assert tag["schema_version"] == TAG_SCHEMA
    validate_tag(tag)
    publication = _publication()
    assert set(publication) == set(PUBLICATION_FIELDS)
    validate_publication(publication)


def aggregate_tag() -> dict[str, object]:
    state = empty_state()
    record = to_record(normalized())
    record["collected_at"] = "2024-02-01T00:00:00Z"
    record["creation_at"] = "2024-01-15T00:00:00Z"
    record["tags"] = ["python"]
    record["record_hash"] = content_hash(record)
    assert observe(state, SilverQuestion.from_record(record))
    return aggregate(state, AS_OF).tag_monthly[0]


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "gold_vitality_monthly.v0"},
        {"creation_month": "2024-1"},
        {"as_of": "2024-03-01T00:00:00+00:00"},
        {"score_min": 0},
        {"is_answered_false_with_answers": 9},
        {"questions": True},
    ],
)
def test_monthly_contract_rejections(change: dict[str, object]) -> None:
    row = _row()
    row.update(change)
    with pytest.raises(ValueError):
        validate_monthly(row)


def test_null_distribution_cannot_become_zero() -> None:
    row = _row()
    row["score_known"] = 0
    row["score_unknown"] = row["questions"]
    row["score_sum"] = 0
    with pytest.raises(ValueError, match="null"):
        validate_monthly(row)


def test_publication_must_stay_descriptive_and_name_its_cutoff_source() -> None:
    publication = _publication()
    publication["causal_claims"] = "supported"
    with pytest.raises(ValueError, match="descriptive"):
        validate_publication(publication)
    derived = _publication()
    derived["as_of_source"] = "derived_max_collected_at"
    validate_publication(derived)
    derived["as_of_source"] = "max_collected_at"
    with pytest.raises(ValueError, match="as_of source"):
        validate_publication(derived)


def test_integer_summaries_reject_empty_or_negative_inputs() -> None:
    with pytest.raises(ValueError):
        nearest_rank([], 500_000)
    assert share_ppm(1, 0) is None
    with pytest.raises(ValueError):
        share_ppm(-1, 1)
    assert hhi_ppm([]) is None
    with pytest.raises(ValueError):
        hhi_ppm([-1])
    with pytest.raises(ValueError):
        stamp(datetime(2024, 3, 1))
