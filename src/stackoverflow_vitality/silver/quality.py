"""Normalization and explicit per-record quality decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.silver.content import clean_body, clean_title
from stackoverflow_vitality.silver.models import (
    SCHEMA_VERSION,
    InputPage,
    QualityEvent,
    SilverQuestion,
    content_hash,
    utc,
)


@dataclass(frozen=True)
class QualityConfig:
    max_body_chars: int = 500_000
    minimum_timestamp: datetime = datetime(2008, 1, 1, tzinfo=UTC)
    future_tolerance_seconds: int = 86400

    def __post_init__(self) -> None:
        if type(self.max_body_chars) is not int or self.max_body_chars < 1:
            raise ValueError("max_body_chars must be positive")
        if type(self.future_tolerance_seconds) is not int or self.future_tolerance_seconds < 0:
            raise ValueError("future tolerance must be nonnegative")
        if self.minimum_timestamp.tzinfo is None:
            raise ValueError("minimum timestamp requires timezone")


def summary(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, (str, list, dict)):
        return f"{type(value).__name__}(length={len(value)})"
    return type(value).__name__


def normalize(
    item: object,
    page: InputPage,
    index: int,
    transformation_run_id: str,
    now: datetime,
    config: QualityConfig,
) -> tuple[SilverQuestion | None, list[QualityEvent]]:
    events: list[QualityEvent] = []
    envelope = page.envelope
    raw = item if isinstance(item, dict) else {}
    identifier = raw.get("question_id")
    source_run = envelope.get("run_id")
    source_page = envelope.get("page")

    def issue(
        rule: str,
        field: str,
        value: object,
        reason: str,
        severity: Literal["error", "warning", "informational"] = "error",
    ) -> None:
        events.append(
            QualityEvent(
                transformation_run_id=transformation_run_id,
                source_run_id=source_run if isinstance(source_run, str) else None,
                source_page=source_page if type(source_page) is int else None,
                question_id=identifier if type(identifier) is int and identifier > 0 else None,
                rule_id=rule,
                severity=severity,
                reason=reason,
                field=field,
                observed_value=summary(value),
                quarantined_at=now,
                source_path=page.path,
                source_item_index=index,
            )
        )

    if not isinstance(item, dict):
        issue("SQ-001", "payload", item, "question payload must be an object")
        return None, events
    if type(identifier) is not int or identifier <= 0:
        issue("SQ-002", "question_id", identifier, "positive integer question_id required")
    collected: datetime | None = None
    try:
        if not isinstance(source_run, str):
            raise ValueError
        UUID(source_run)
        if type(source_page) is not int or source_page < 1:
            raise ValueError
        for field in ("checksum",):
            if not isinstance(envelope.get(field), str) or not re.fullmatch(
                r"[0-9a-f]{64}", envelope[field]
            ):
                raise ValueError
        if not isinstance(envelope.get("site"), str) or not envelope["site"].strip():
            raise ValueError
        collected = utc(envelope["recovered_at"])
        if (
            not config.minimum_timestamp
            <= collected
            <= now + timedelta(seconds=config.future_tolerance_seconds)
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        issue("SQ-003", "lineage", None, "complete, plausible Bronze lineage required")
    values: dict[str, Any] = {}
    for source, target in (
        ("creation_date", "creation_at"),
        ("last_activity_date", "last_activity_at"),
        ("last_edit_date", "last_edit_at"),
        ("closed_date", "closed_at"),
    ):
        value = raw.get(source)
        values[target] = None
        if value is None and source != "creation_date":
            continue
        try:
            if type(value) is not int:
                raise ValueError
            parsed = datetime.fromtimestamp(value, UTC)
            upper = min(now, collected or now) + timedelta(seconds=config.future_tolerance_seconds)
            if not config.minimum_timestamp <= parsed <= upper:
                raise ValueError
            values[target] = parsed
        except (ValueError, OverflowError, OSError):
            issue("SQ-004", source, value, "required or supplied epoch must be plausible UTC")
    creation = values["creation_at"]
    for field in ("last_activity_at", "last_edit_at", "closed_at"):
        if creation and values[field] and values[field] < creation:
            issue("SQ-005", field, raw.get(field), "timestamp precedes creation")
    for field in ("score", "view_count", "answer_count", "accepted_answer_id"):
        value = raw.get(field)
        values[field] = value
        if value is not None and (
            type(value) is not int
            or (field in {"view_count", "answer_count"} and value < 0)
            or (field == "accepted_answer_id" and value <= 0)
        ):
            issue("SQ-006", field, value, "invalid integer, counter or identifier")
    owner = raw.get("owner")
    values["owner_user_id"] = None
    if owner is not None:
        if not isinstance(owner, dict):
            issue("SQ-007", "owner", owner, "owner must be an object when supplied")
        else:
            owner_id = owner.get("user_id")
            if owner_id is not None and type(owner_id) is not int:
                issue("SQ-007", "owner_user_id", owner_id, "owner identifier must be integer")
            else:
                values["owner_user_id"] = owner_id
    tags = raw.get("tags")
    values["tags"] = None
    if tags is not None:
        if not isinstance(tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in tags
        ):
            issue("SQ-008", "tags", tags, "tags must be a list of nonempty strings")
        else:
            values["tags"] = tuple(sorted(set(tags)))
    for source, target in (
        ("title", "title_raw"),
        ("body", "body_html"),
        ("content_license", "content_license"),
        ("link", "link"),
        ("closed_reason", "closed_reason"),
    ):
        value = raw.get(source)
        values[target] = value
        if value is not None and not isinstance(value, str):
            issue("SQ-009", source, value, "text field must be string or null")
    body = values["body_html"]
    if isinstance(body, str) and len(body) > config.max_body_chars:
        issue("SQ-010", "body", body, "body exceeds configured character limit")
    if body is None:
        issue("SQ-011", "body", body, "body not supplied by source filter", "informational")
    answered = raw.get("is_answered")
    values["is_answered"] = answered
    if answered is not None and type(answered) is not bool:
        issue("SQ-012", "is_answered", answered, "is_answered must be boolean or null")
    count = values["answer_count"]
    accepted = values["accepted_answer_id"]
    if (answered is True and count == 0) or (
        accepted is not None and (answered is False or count == 0)
    ):
        issue(
            "SQ-013",
            "is_answered",
            answered,
            "answer state and supplied related fields disagree",
            "warning",
        )
    # is_answered=False with answer_count>0 is legitimate: answers need not satisfy resolution.
    if any(event.severity == "error" for event in events):
        return None, events
    assert collected is not None
    values["body_text"], values["code_blocks"] = clean_body(body)
    values["title_clean"] = clean_title(values["title_raw"])
    values.update(
        question_id=identifier,
        site=envelope["site"],
        collected_at=collected,
        source_run_id=source_run,
        source_page=source_page,
        source_checksum=envelope["checksum"],
        source_file_checksum=page.file_checksum,
        source_path=page.path,
        source_item_index=index,
        schema_version=SCHEMA_VERSION,
        valid_from=collected,
        valid_to=None,
        is_current=True,
    )
    serialized = to_record(values)
    values["record_hash"] = content_hash(serialized)
    return SilverQuestion(**values), events
