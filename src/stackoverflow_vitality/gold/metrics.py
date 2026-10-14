"""Select one question state at as_of and aggregate descriptive counts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from stackoverflow_vitality.gold.models import (
    MONTHLY_SCHEMA,
    TAG_SCHEMA,
    distribution,
    exposure_fields,
    hhi_ppm,
    share_ppm,
    stamp,
    validate_monthly,
    validate_tag,
)
from stackoverflow_vitality.silver.history import rebuild
from stackoverflow_vitality.silver.models import utc

_MISSING = "without_observation"
_AFTER = "created_after_as_of"


@dataclass(frozen=True)
class QuestionView:
    site: str
    creation_month: str
    score: int | None
    view_count: int | None
    answer_count: int | None
    is_answered: bool | None
    accepted_answer_id: int | None
    closed_at: str | None
    tags: tuple[str, ...] | None
    exposure_seconds: int


@dataclass(frozen=True)
class Aggregation:
    as_of: datetime
    questions_included: int
    questions_without_observation: int
    questions_created_after_as_of: int
    monthly: tuple[dict[str, Any], ...]
    tag_monthly: tuple[dict[str, Any], ...]


@dataclass
class _Bucket:
    source_true: int = 0
    source_false: int = 0
    source_unknown: int = 0
    answers_true: int = 0
    answers_false: int = 0
    answers_unknown: int = 0
    accepted_true: int = 0
    accepted_false: int = 0
    false_with_answers: int = 0
    true_without_answers: int = 0
    accepted_without_answers: int = 0
    closed_known: int = 0
    closed_unknown: int = 0
    missing_tags: int = 0
    scores: list[int] = field(default_factory=list)
    score_unknown: int = 0
    views: list[int] = field(default_factory=list)
    view_unknown: int = 0
    answers: list[int] = field(default_factory=list)
    exposures: list[int] = field(default_factory=list)
    tags: Counter[str] = field(default_factory=Counter)

    def add(self, view: QuestionView) -> None:
        if view.is_answered is True:
            self.source_true += 1
        elif view.is_answered is False:
            self.source_false += 1
        else:
            self.source_unknown += 1
        if view.answer_count is None:
            self.answers_unknown += 1
        elif view.answer_count > 0:
            self.answers_true += 1
            self.answers.append(view.answer_count)
        elif view.answer_count == 0:
            self.answers_false += 1
            self.answers.append(view.answer_count)
        else:
            raise ValueError("negative answer_count")
        # An absent accepted id means no id was observed. It is not an unknown boolean.
        if view.accepted_answer_id is None:
            self.accepted_false += 1
        else:
            self.accepted_true += 1
        if view.is_answered is False and view.answer_count is not None and view.answer_count > 0:
            self.false_with_answers += 1
        if view.is_answered is True and view.answer_count == 0:
            self.true_without_answers += 1
        if view.accepted_answer_id is not None and view.answer_count == 0:
            self.accepted_without_answers += 1
        if view.closed_at is None:
            self.closed_unknown += 1
        else:
            self.closed_known += 1
        _append(self.scores, view.score, "score")
        if view.score is None:
            self.score_unknown += 1
        if view.view_count is not None and view.view_count < 0:
            raise ValueError("negative view_count")
        _append(self.views, view.view_count, "view")
        if view.view_count is None:
            self.view_unknown += 1
        if view.tags is None:
            self.missing_tags += 1
        else:
            self.tags.update(view.tags)
        self.exposures.append(view.exposure_seconds)


def _append(values: list[int], value: int | None, label: str) -> None:
    if value is None:
        return
    if type(value) is not int:
        raise ValueError(f"invalid {label}")
    values.append(value)


def aggregate(state: dict[str, Any], as_of: datetime) -> Aggregation:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of requires a UTC timestamp")
    resolved = as_of.astimezone(UTC)
    selected, without, created_after = _select(state, resolved)
    buckets: dict[tuple[str, str], _Bucket] = {}
    for view in selected:
        buckets.setdefault((view.site, view.creation_month), _Bucket()).add(view)
    as_of_text = stamp(resolved)
    monthly: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    for site, month in sorted(buckets):
        bucket = buckets[(site, month)]
        monthly.append(_monthly_row(site, month, as_of_text, bucket))
        tags.extend(_tag_rows(site, month, as_of_text, bucket))
    for row in monthly:
        validate_monthly(row)
    for row in tags:
        validate_tag(row)
    _check_tag_totals(monthly, tags)
    return Aggregation(
        resolved,
        len(selected),
        without,
        created_after,
        tuple(monthly),
        tuple(tags),
    )


def latest_collected_at(state: dict[str, Any]) -> datetime:
    collected = [_timestamp(item, "collected_at") for item in _observations(state)]
    if not collected:
        raise ValueError("as_of is required when Silver has no observations")
    return max(collected)


def _select(state: dict[str, Any], as_of: datetime) -> tuple[tuple[QuestionView, ...], int, int]:
    rebuild(state)
    catalog = state.get("catalog")
    history = state.get("questions")
    if not isinstance(catalog, dict) or not isinstance(history, list):
        raise ValueError("Silver catalog is unreadable")
    versions: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for version in history:
        if not isinstance(version, dict):
            raise ValueError("Silver history is unreadable")
        versions.setdefault((_text(version, "site"), _integer(version, "question_id")), []).append(
            version
        )
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for observation in _observations(state):
        grouped.setdefault(
            (_text(observation, "site"), _integer(observation, "question_id")), []
        ).append(observation)
    selected: list[QuestionView] = []
    without = 0
    created_after = 0
    for key in sorted(grouped):
        view = _view_at(grouped[key], versions.get(key, []), catalog, as_of)
        if view is _MISSING:
            without += 1
        elif view is _AFTER:
            created_after += 1
        elif isinstance(view, QuestionView):
            selected.append(view)
        else:
            raise ValueError("unreadable question selection")
    return tuple(selected), without, created_after


def _view_at(
    observations: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    catalog: dict[Any, Any],
    as_of: datetime,
) -> QuestionView | str:
    eligible = [item for item in observations if _timestamp(item, "collected_at") <= as_of]
    if not eligible:
        return _MISSING
    metric = max(
        eligible,
        key=lambda item: (
            _text(item, "collected_at"),
            _text(item, "record_hash"),
            _text(item, "observation_id"),
        ),
    )
    content = _content_at(versions, as_of)
    if content is None or not isinstance(catalog.get(content.get("record_hash")), dict):
        raise ValueError("content version missing at as_of")
    site = _text(metric, "site")
    question_id = _integer(metric, "question_id")
    if content.get("site") != site or content.get("question_id") != question_id:
        raise ValueError("Silver content identity disagrees with the observation")
    created = _timestamp(content, "creation_at")
    if created > as_of:
        return _AFTER
    delta = as_of - created
    if delta.microseconds:
        raise ValueError("exposure requires whole seconds")
    return QuestionView(
        site=site,
        creation_month=f"{created.year:04d}-{created.month:02d}",
        score=_optional_int(metric, "score"),
        view_count=_optional_int(metric, "view_count"),
        answer_count=_optional_int(metric, "answer_count"),
        is_answered=_optional_bool(metric, "is_answered"),
        accepted_answer_id=_optional_int(metric, "accepted_answer_id"),
        closed_at=_optional_stamp(content, "closed_at"),
        tags=_tags(content),
        exposure_seconds=delta.days * 86400 + delta.seconds,
    )


def _content_at(versions: list[dict[str, Any]], as_of: datetime) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    for version in versions:
        start = _timestamp(version, "valid_from")
        end = version.get("valid_to")
        if start <= as_of and (end is None or as_of < _timestamp_text(end, "valid_to")):
            if found is not None:
                raise ValueError("overlapping Silver content versions")
            found = version
    return found


def _monthly_row(site: str, month: str, as_of: str, bucket: _Bucket) -> dict[str, Any]:
    questions = bucket.source_true + bucket.source_false + bucket.source_unknown
    with_tags = questions - bucket.missing_tags
    counts = list(bucket.tags.values())
    maximum = max(counts) if counts else 0
    row: dict[str, Any] = {
        "schema_version": MONTHLY_SCHEMA,
        "site": site,
        "creation_month": month,
        "as_of": as_of,
        "questions": questions,
        "source_is_answered_true": bucket.source_true,
        "source_is_answered_false": bucket.source_false,
        "source_is_answered_unknown": bucket.source_unknown,
        "has_answers_true": bucket.answers_true,
        "has_answers_false": bucket.answers_false,
        "has_answers_unknown": bucket.answers_unknown,
        "has_accepted_answer_true": bucket.accepted_true,
        "has_accepted_answer_false": bucket.accepted_false,
        "is_answered_false_with_answers": bucket.false_with_answers,
        "is_answered_true_without_answers": bucket.true_without_answers,
        "accepted_without_answers": bucket.accepted_without_answers,
        "closed_known": bucket.closed_known,
        "closed_unknown": bucket.closed_unknown,
        "questions_with_tags": with_tags,
        "questions_missing_tags": bucket.missing_tags,
        "tag_assignments": sum(counts),
        "distinct_tags": len(counts),
        "max_tag_questions": maximum,
        "max_tag_question_share_ppm": share_ppm(maximum, with_tags),
        "tag_assignment_hhi_ppm": hhi_ppm(counts),
    }
    row.update(distribution("score", bucket.scores, bucket.score_unknown))
    row.update(distribution("view_count", bucket.views, bucket.view_unknown))
    row.update(distribution("answer_count", bucket.answers, bucket.answers_unknown))
    row.update(exposure_fields(bucket.exposures))
    return row


def _tag_rows(site: str, month: str, as_of: str, bucket: _Bucket) -> list[dict[str, Any]]:
    assignments = sum(bucket.tags.values())
    return [
        {
            "schema_version": TAG_SCHEMA,
            "site": site,
            "creation_month": month,
            "tag": tag,
            "as_of": as_of,
            "questions": bucket.tags[tag],
            "assignment_share_ppm": share_ppm(bucket.tags[tag], assignments),
        }
        for tag in sorted(bucket.tags)
    ]


def _check_tag_totals(monthly: list[dict[str, Any]], tags: list[dict[str, Any]]) -> None:
    for row in monthly:
        related = [
            item
            for item in tags
            if item["site"] == row["site"]
            and item["creation_month"] == row["creation_month"]
            and item["as_of"] == row["as_of"]
        ]
        if len(related) != row["distinct_tags"]:
            raise ValueError("tag rows do not match distinct tags")
        if sum(item["questions"] for item in related) != row["tag_assignments"]:
            raise ValueError("tag rows do not match assignments")


def _observations(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get("observations")
    if not isinstance(raw, dict):
        raise ValueError("Silver observations are unreadable")
    records: list[dict[str, Any]] = []
    for item in raw.values():
        if not isinstance(item, dict):
            raise ValueError("Silver observation is unreadable")
        records.append(item)
    return records


def _text(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid Silver field {key}")
    return value


def _integer(record: dict[str, Any], key: str) -> int:
    value = record.get(key)
    if type(value) is not int:
        raise ValueError(f"invalid Silver field {key}")
    return value


def _optional_int(record: dict[str, Any], key: str) -> int | None:
    if key not in record or record[key] is None:
        return None
    return _integer(record, key)


def _optional_bool(record: dict[str, Any], key: str) -> bool | None:
    if key not in record or record[key] is None:
        return None
    value = record[key]
    if type(value) is not bool:
        raise ValueError(f"invalid Silver field {key}")
    return value


def _optional_stamp(record: dict[str, Any], key: str) -> str | None:
    if key not in record or record[key] is None:
        return None
    return _validated_stamp(record[key], key)


def _validated_stamp(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid Silver field {key}")
    _timestamp_text(value, key)
    return value


def _timestamp(record: dict[str, Any], key: str) -> datetime:
    return _timestamp_text(record.get(key), key)


def _timestamp_text(value: object, key: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"invalid Silver field {key}")
    try:
        parsed = utc(value)
    except ValueError:
        raise ValueError(f"invalid Silver field {key}") from None
    return parsed


def _tags(record: dict[str, Any]) -> tuple[str, ...] | None:
    value = record.get("tags")
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or list(value) != sorted(set(value))
    ):
        raise ValueError("invalid Silver tags")
    return tuple(value)
