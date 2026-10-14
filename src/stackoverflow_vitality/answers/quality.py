"""Answer quality rules. Null score and acceptance stay null."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from stackoverflow_vitality.silver.content import clean_body

CONTENT_SCHEMA = "silver_answer_content_versions.v1"
METRIC_SCHEMA = "silver_answer_metric_observations.v1"
CATALOG_SCHEMA = "silver_answer_catalog.v1"
ENTITY_TYPE = "answer"
CONTENT_FIELDS = (
    "schema_version",
    "entity_type",
    "site",
    "answer_id",
    "question_id",
    "creation_at",
    "last_edit_at",
    "owner_user_id",
    "body_html",
    "content_license",
)
MINIMUM_TIMESTAMP = datetime(2008, 1, 1, tzinfo=UTC)
FUTURE_TOLERANCE_SECONDS = 86400
_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AnswerQualityEvent:
    rule_id: str
    severity: Literal["error", "warning", "informational"]
    field: str
    reason: str
    answer_id: int | None
    question_id: int | None
    source_item_index: int

    def record(self, transformation_run_id: str, source_run_id: str | None) -> dict[str, Any]:
        return {
            "schema_version": "silver_answer_quality.v1",
            "transformation_run_id": transformation_run_id,
            "source_run_id": source_run_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "field": self.field,
            "reason": self.reason,
            "answer_id": self.answer_id,
            "question_id": self.question_id,
            "source_item_index": self.source_item_index,
        }


def normalize_answer(
    item: object,
    envelope: dict[str, Any],
    index: int,
    *,
    now: datetime,
    max_body_chars: int,
) -> tuple[dict[str, Any] | None, list[AnswerQualityEvent]]:
    events: list[AnswerQualityEvent] = []
    raw = item if isinstance(item, dict) else {}
    answer_id = raw.get("answer_id")
    question_id = raw.get("question_id")
    valid_answer = type(answer_id) is int and answer_id > 0
    valid_question = type(question_id) is int and question_id > 0

    def issue(
        rule: str,
        field: str,
        reason: str,
        severity: Literal["error", "warning", "informational"] = "error",
    ) -> None:
        events.append(
            AnswerQualityEvent(
                rule_id=rule,
                severity=severity,
                field=field,
                reason=reason,
                answer_id=answer_id if valid_answer else None,
                question_id=question_id if valid_question else None,
                source_item_index=index,
            )
        )

    if not isinstance(item, dict):
        issue("SA-001", "answer_id", "answer payload must be an object")
        return None, events
    if not valid_answer:
        issue("SA-001", "answer_id", "positive integer answer_id required")
    if not valid_question:
        issue("SA-002", "question_id", "positive integer question_id required")
    requested = envelope.get("question_ids")
    if valid_question and (not isinstance(requested, list) or question_id not in requested):
        issue("SA-003", "question_id", "answer question_id is outside the requested batch")
    collected = _collected(envelope)
    if collected is None:
        issue("SA-004", "recovered_at", "collected_at must be a timezone-aware UTC timestamp")
    creation = _epoch("creation_date", raw.get("creation_date"), collected, now, required=True)
    if creation is None:
        issue("SA-004", "creation_date", "creation_at must be a plausible UTC epoch")
    elif collected is not None and creation > collected:
        issue("SA-005", "creation_at", "creation_at is after collected_at")
    last_edit = _optional_epoch("last_edit_date", raw, collected, now, creation, issue)
    last_activity = _optional_epoch("last_activity_date", raw, collected, now, creation, issue)
    score = _score(raw, issue)
    accepted = _accepted(raw, issue)
    owner = _owner(raw, issue)
    body = raw.get("body")
    if type(body) is not str or not body.strip():
        issue("SA-007", "body", "answer body is required")
        body_html = None
    elif len(body) > max_body_chars:
        issue("SA-007", "body", "answer body exceeds the configured limit")
        body_html = None
    else:
        body_html = body
    license_value = raw.get("content_license")
    if license_value is not None and type(license_value) is not str:
        issue("SA-004", "content_license", "content license must be a string or null")
        license_value = None
    if any(event.severity == "error" for event in events):
        return None, events
    assert valid_answer and valid_question and collected is not None and creation is not None
    assert isinstance(body_html, str)
    text, blocks = clean_body(body_html)
    record: dict[str, Any] = {
        "schema_version": CONTENT_SCHEMA,
        "entity_type": ENTITY_TYPE,
        "site": envelope["site"],
        "answer_id": answer_id,
        "question_id": question_id,
        "creation_at": _iso(creation),
        "last_edit_at": None if last_edit is None else _iso(last_edit),
        "last_activity_at": None if last_activity is None else _iso(last_activity),
        "owner_user_id": owner,
        "body_html": body_html,
        "body_text": text,
        "code_blocks": list(blocks or ()),
        "content_license": license_value,
        "collected_at": _iso(collected),
        "score": score,
        "is_accepted": accepted,
        "source_run_id": envelope["run_id"],
        "source_page": envelope["page"],
        "source_batch_id": envelope["batch_id"],
        "source_file_checksum": envelope["checksum"],
        "source_item_index": index,
        "warnings": [event.rule_id for event in events if event.severity == "warning"],
    }
    return record, events


def _collected(envelope: dict[str, Any]) -> datetime | None:
    value = envelope.get("recovered_at")
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    try:
        UUID(str(envelope.get("run_id")))
    except (TypeError, ValueError):
        return None
    if type(envelope.get("page")) is not int or envelope["page"] < 1:
        return None
    if not isinstance(envelope.get("checksum"), str) or not _HEX.fullmatch(envelope["checksum"]):
        return None
    if not isinstance(envelope.get("site"), str) or not envelope["site"].strip():
        return None
    return parsed.astimezone(UTC)


def _epoch(
    _field: str,
    value: object,
    collected: datetime | None,
    now: datetime,
    *,
    required: bool,
) -> datetime | None:
    if value is None and not required:
        return None
    if type(value) is not int:
        return None
    try:
        parsed = datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    upper_anchor = collected or now
    upper = min(now, upper_anchor) + timedelta(seconds=FUTURE_TOLERANCE_SECONDS)
    if not MINIMUM_TIMESTAMP <= parsed <= upper:
        return None
    return parsed


def _optional_epoch(
    field: str,
    raw: dict[str, Any],
    collected: datetime | None,
    now: datetime,
    creation: datetime | None,
    issue: Any,
) -> datetime | None:
    if field not in raw or raw.get(field) is None:
        return None
    parsed = _epoch(field, raw.get(field), collected, now, required=False)
    if parsed is None:
        issue("SA-004", field, "timestamp must be a plausible UTC epoch")
        return None
    if creation is not None and parsed < creation:
        issue("SA-004", field, "timestamp precedes creation_at")
        return None
    return parsed


def _score(raw: dict[str, Any], issue: Any) -> int | None:
    if "score" not in raw or raw.get("score") is None:
        return None
    value = raw.get("score")
    if type(value) is not int:
        issue("SA-006", "score", "score must be an integer or null")
        return None
    return value


def _accepted(raw: dict[str, Any], issue: Any) -> bool | None:
    if "is_accepted" not in raw or raw.get("is_accepted") is None:
        return None
    value = raw.get("is_accepted")
    if type(value) is not bool:
        issue("SA-006", "is_accepted", "is_accepted must be a boolean or null")
        return None
    return value


def _owner(raw: dict[str, Any], issue: Any) -> int | None:
    owner = raw.get("owner")
    if not isinstance(owner, dict) or "user_id" not in owner or owner.get("user_id") is None:
        issue("SA-010", "owner_user_id", "owner is absent; user id stays null", "warning")
        return None
    user_id = owner.get("user_id")
    if type(user_id) is not int or user_id <= 0:
        issue("SA-010", "owner_user_id", "owner user id must be a positive integer or null")
        return None
    return user_id


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
