"""Versioned Gold v1 field sets and deterministic integer summaries."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

MONTHLY_SCHEMA = "gold_vitality_monthly.v1"
TAG_SCHEMA = "gold_vitality_tag_monthly.v1"
PUBLICATION_SCHEMA = "gold_vitality_publication.v1"
TRANSFORMER_VERSION = "1.0.0"
PPM = 1_000_000
CAUSAL_CLAIMS = "unsupported"
LIMITATION = (
    "Gold v1 describes question state observed at as_of. It does not estimate time to "
    "answer, survival, or a causal effect. Causal conclusions require a statistical design, "
    "controls, adequate historical coverage, and complementary sources."
)
MONTH_PATTERN = re.compile(r"\d{4}-\d{2}")
DISTRIBUTION_SUFFIXES = ("known", "unknown", "min", "p50", "p90", "max", "sum")
MONTHLY_FIELDS = (
    "schema_version",
    "site",
    "creation_month",
    "as_of",
    "questions",
    "source_is_answered_true",
    "source_is_answered_false",
    "source_is_answered_unknown",
    "has_answers_true",
    "has_answers_false",
    "has_answers_unknown",
    "has_accepted_answer_true",
    "has_accepted_answer_false",
    "is_answered_false_with_answers",
    "is_answered_true_without_answers",
    "accepted_without_answers",
    "closed_known",
    "closed_unknown",
    *(f"score_{suffix}" for suffix in DISTRIBUTION_SUFFIXES),
    *(f"view_count_{suffix}" for suffix in DISTRIBUTION_SUFFIXES),
    *(f"answer_count_{suffix}" for suffix in DISTRIBUTION_SUFFIXES),
    "questions_with_tags",
    "questions_missing_tags",
    "tag_assignments",
    "distinct_tags",
    "max_tag_questions",
    "max_tag_question_share_ppm",
    "tag_assignment_hhi_ppm",
    "exposure_seconds_min",
    "exposure_seconds_p50",
    "exposure_seconds_max",
)
TAG_FIELDS = (
    "schema_version",
    "site",
    "creation_month",
    "tag",
    "as_of",
    "questions",
    "assignment_share_ppm",
)
IDENTITY_FIELDS = (
    "schema_version",
    "transformer_version",
    "silver_schema_version",
    "silver_dataset_id",
    "silver_commit_sequence",
    "silver_transformation_run_id",
    "silver_input_records",
    "silver_accepted_records",
    "silver_quarantined_records",
    "silver_quality_errors",
    "silver_quality_warnings",
    "as_of",
    "as_of_source",
    "questions_included",
    "questions_without_observation",
    "questions_created_after_as_of",
    "monthly_rows",
    "tag_rows",
    "output_checksums",
    "descriptive_only",
    "causal_claims",
    "limitation",
)
PUBLICATION_FIELDS = (*IDENTITY_FIELDS, "publication_id", "output_paths")


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of requires a UTC timestamp")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def nearest_rank(ordered: list[int], proportion_ppm: int) -> int:
    """1-based ceil(n * proportion) on an ascending list; proportion is parts per million."""
    count = len(ordered)
    if count == 0 or not 0 < proportion_ppm <= PPM:
        raise ValueError("percentile requires a non-empty population")
    rank = min(max((count * proportion_ppm + PPM - 1) // PPM, 1), count)
    return ordered[rank - 1]


def share_ppm(count: int, total: int) -> int | None:
    if total == 0:
        return None
    if count < 0 or total < 0:
        raise ValueError("negative share")
    return (count * PPM) // total


def hhi_ppm(counts: list[int]) -> int | None:
    """Sum of floor(count^2 * PPM / total^2). Terms are floored before the sum."""
    total = sum(counts)
    if total == 0:
        return None
    if total < 0 or any(count < 0 for count in counts):
        raise ValueError("negative concentration")
    denominator = total * total
    return sum((count * count * PPM) // denominator for count in counts)


def distribution(name: str, values: list[int], unknown: int) -> dict[str, int | None]:
    ordered = sorted(values)
    if ordered:
        summary: dict[str, int | None] = {
            "min": ordered[0],
            "p50": nearest_rank(ordered, 500_000),
            "p90": nearest_rank(ordered, 900_000),
            "max": ordered[-1],
            "sum": sum(ordered),
        }
    else:
        summary = {"min": None, "p50": None, "p90": None, "max": None, "sum": None}
    fields: dict[str, int | None] = {f"{name}_known": len(ordered), f"{name}_unknown": unknown}
    fields.update({f"{name}_{key}": value for key, value in summary.items()})
    return fields


def exposure_fields(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("exposure requires questions")
    return {
        "exposure_seconds_min": ordered[0],
        "exposure_seconds_p50": nearest_rank(ordered, 500_000),
        "exposure_seconds_max": ordered[-1],
    }


def _nonnegative(row: dict[str, Any], key: str) -> int:
    value = row[key]
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid count {key}")
    return value


def _ppm(value: int | None) -> int:
    if type(value) is not int:
        raise ValueError("invalid tag concentration")
    if not 0 <= value <= PPM:
        raise ValueError("invalid tag concentration")
    return value


def _required_int(value: int | None, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} statistics are required")
    return value


def _optional_int(row: dict[str, Any], key: str) -> int | None:
    value = row[key]
    if value is None or type(value) is int:
        return value
    raise ValueError(f"invalid statistic {key}")


def _check_distribution(row: dict[str, Any], name: str, *, allow_negative: bool) -> None:
    known = _nonnegative(row, f"{name}_known")
    unknown = _nonnegative(row, f"{name}_unknown")
    if known + unknown != row["questions"]:
        raise ValueError(f"{name} coverage is incomplete")
    stats = [
        _optional_int(row, f"{name}_{suffix}") for suffix in ("min", "p50", "p90", "max", "sum")
    ]
    if known == 0:
        if any(item is not None for item in stats):
            raise ValueError(f"{name} statistics must be null without known values")
        return
    numbers = [_required_int(item, name) for item in stats]
    low, p50, p90, high, total = numbers[0], numbers[1], numbers[2], numbers[3], numbers[4]
    if not low <= p50 <= p90 <= high:
        raise ValueError(f"{name} percentiles are unordered")
    if not allow_negative and (low < 0 or total < 0):
        raise ValueError(f"{name} cannot be negative")


def validate_monthly(row: dict[str, Any]) -> None:
    if set(row) != set(MONTHLY_FIELDS):
        missing = sorted(set(MONTHLY_FIELDS) - set(row))
        extra = sorted(set(row) - set(MONTHLY_FIELDS))
        raise ValueError(f"monthly fields drifted: missing={missing} extra={extra}")
    if row["schema_version"] != MONTHLY_SCHEMA:
        raise ValueError("unsupported monthly schema")
    if not isinstance(row["site"], str) or not row["site"]:
        raise ValueError("invalid site")
    if not isinstance(row["creation_month"], str) or not MONTH_PATTERN.fullmatch(
        row["creation_month"]
    ):
        raise ValueError("invalid creation month")
    if not isinstance(row["as_of"], str) or not row["as_of"].endswith("Z"):
        raise ValueError("invalid as_of")
    questions = _nonnegative(row, "questions")
    if questions < 1:
        raise ValueError("monthly row must contain questions")
    partitions = (
        ("source_is_answered_true", "source_is_answered_false", "source_is_answered_unknown"),
        ("has_answers_true", "has_answers_false", "has_answers_unknown"),
        ("has_accepted_answer_true", "has_accepted_answer_false"),
        ("closed_known", "closed_unknown"),
        ("questions_with_tags", "questions_missing_tags"),
    )
    for keys in partitions:
        if sum(_nonnegative(row, key) for key in keys) != questions:
            raise ValueError("counts do not cover the population")
    if _nonnegative(row, "is_answered_false_with_answers") > min(
        row["source_is_answered_false"], row["has_answers_true"]
    ):
        raise ValueError("false-with-answers exceeds its signals")
    if _nonnegative(row, "is_answered_true_without_answers") > min(
        row["source_is_answered_true"], row["has_answers_false"]
    ):
        raise ValueError("true-without-answers exceeds its signals")
    if _nonnegative(row, "accepted_without_answers") > min(
        row["has_accepted_answer_true"], row["has_answers_false"]
    ):
        raise ValueError("accepted-without-answers exceeds its signals")
    _check_distribution(row, "score", allow_negative=True)
    _check_distribution(row, "view_count", allow_negative=False)
    _check_distribution(row, "answer_count", allow_negative=False)
    if row["answer_count_known"] != row["has_answers_true"] + row["has_answers_false"]:
        raise ValueError("answer_count coverage disagrees with has_answers")
    if row["answer_count_unknown"] != row["has_answers_unknown"]:
        raise ValueError("answer_count coverage disagrees with has_answers")
    with_tags = _nonnegative(row, "questions_with_tags")
    assignments = _nonnegative(row, "tag_assignments")
    distinct = _nonnegative(row, "distinct_tags")
    maximum = _nonnegative(row, "max_tag_questions")
    share = _optional_int(row, "max_tag_question_share_ppm")
    concentration = _optional_int(row, "tag_assignment_hhi_ppm")
    if with_tags == 0:
        if not (
            assignments == distinct == maximum == 0 and share is None and concentration is None
        ):
            raise ValueError("missing tags cannot invent concentration")
    elif assignments < with_tags or distinct < 1 or not 1 <= maximum <= with_tags:
        raise ValueError("invalid tag concentration")
    else:
        _ppm(share)
        _ppm(concentration)
    exposure = [
        _required_int(_optional_int(row, key), "exposure")
        for key in ("exposure_seconds_min", "exposure_seconds_p50", "exposure_seconds_max")
    ]
    low, middle, high = exposure[0], exposure[1], exposure[2]
    if not 0 <= low <= middle <= high:
        raise ValueError("exposure percentiles are unordered")


def validate_tag(row: dict[str, Any]) -> None:
    if set(row) != set(TAG_FIELDS):
        raise ValueError("tag fields drifted")
    if row["schema_version"] != TAG_SCHEMA:
        raise ValueError("unsupported tag schema")
    if not isinstance(row["site"], str) or not row["site"]:
        raise ValueError("invalid site")
    if not isinstance(row["tag"], str) or not row["tag"]:
        raise ValueError("invalid tag")
    if not isinstance(row["creation_month"], str) or not MONTH_PATTERN.fullmatch(
        row["creation_month"]
    ):
        raise ValueError("invalid creation month")
    if not isinstance(row["as_of"], str) or not row["as_of"].endswith("Z"):
        raise ValueError("invalid as_of")
    if _nonnegative(row, "questions") < 1:
        raise ValueError("tag row must contain questions")
    share = row["assignment_share_ppm"]
    if type(share) is not int or not 0 <= share <= PPM:
        raise ValueError("invalid assignment share")


def validate_publication(row: dict[str, Any]) -> None:
    if set(row) != set(PUBLICATION_FIELDS):
        raise ValueError("publication fields drifted")
    if (
        row["schema_version"] != PUBLICATION_SCHEMA
        or row["transformer_version"] != TRANSFORMER_VERSION
    ):
        raise ValueError("unsupported Gold publication")
    if row["descriptive_only"] is not True or row["causal_claims"] != CAUSAL_CLAIMS:
        raise ValueError("Gold publication must remain descriptive")
    if row["limitation"] != LIMITATION:
        raise ValueError("Gold limitation text drifted")
    if row["as_of_source"] not in {"explicit", "derived_max_collected_at"}:
        raise ValueError("invalid as_of source")
    if not isinstance(row["as_of"], str) or not row["as_of"].endswith("Z"):
        raise ValueError("invalid as_of")
    for key in (
        "silver_commit_sequence",
        "silver_input_records",
        "silver_accepted_records",
        "silver_quarantined_records",
        "silver_quality_errors",
        "silver_quality_warnings",
        "questions_included",
        "questions_without_observation",
        "questions_created_after_as_of",
        "monthly_rows",
        "tag_rows",
    ):
        _nonnegative(row, key)
    for key in (
        "silver_schema_version",
        "silver_dataset_id",
        "silver_transformation_run_id",
        "publication_id",
    ):
        if not isinstance(row[key], str) or not row[key]:
            raise ValueError(f"invalid publication identity {key}")
    checksums = row["output_checksums"]
    paths = row["output_paths"]
    if not isinstance(checksums, dict) or not isinstance(paths, dict):
        raise ValueError("invalid publication outputs")
    if set(checksums) != {"monthly", "tag_monthly"} or set(paths) != {"monthly", "tag_monthly"}:
        raise ValueError("invalid publication outputs")
    if any(type(value) is not str or not value for value in (*checksums.values(), *paths.values())):
        raise ValueError("invalid publication outputs")
