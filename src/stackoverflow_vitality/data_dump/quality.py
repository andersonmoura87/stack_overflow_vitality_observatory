"""Row decisions for the DD-* rules that apply during ingest."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stackoverflow_vitality.data_dump.dates import EARLIEST, parse_dump_instant, stamp
from stackoverflow_vitality.data_dump.identity import (
    CONTRACT_VERSION,
    PARSER_VERSION,
    SOURCE_KIND,
    digest,
)
from stackoverflow_vitality.storage.files import canonical

_INTEGER = re.compile(r"^[0-9]+$")
_SIGNED = re.compile(r"^-?[0-9]+$")
_DATES = {
    "CreationDate": "source_created_at",
    "LastEditDate": "last_edit_at",
    "LastActivityDate": "last_activity_at",
    "ClosedDate": "closed_at",
}
_IDENTIFIERS = {
    "ParentId": "parent_id",
    "AcceptedAnswerId": "accepted_answer_id",
    "OwnerUserId": "owner_user_id",
}
_COUNTS = {
    "ViewCount": "view_count",
    "AnswerCount": "answer_count",
    "CommentCount": "comment_count",
}


@dataclass(frozen=True)
class RowContext:
    site: str
    ingestion_identity: str
    source_file_name: str
    source_sha256: str
    source_file_size: int
    dump_publication_id: str
    selected: frozenset[int]
    max_attribute_bytes: int
    max_body_chars: int
    observed_at: datetime
    cutoff: datetime | None


@dataclass(frozen=True)
class RowOutcome:
    action: str
    code: str
    record: dict[str, Any] | None = None
    field: str | None = None
    post_id: int | None = None
    post_type_id: int | None = None
    parent_id: int | None = None
    fingerprint: str | None = None
    detail: str | None = None
    warnings: tuple[str, ...] = ()


def evaluate_row(row_number: int, attributes: dict[str, str], context: RowContext) -> RowOutcome:
    for key, value in attributes.items():
        if _too_large(key, context.max_attribute_bytes) or _too_large(
            value, context.max_attribute_bytes
        ):
            return RowOutcome("abort", "DD-021", field=key)
    post_id, id_error = _parse_id(attributes.get("Id"))
    if id_error:
        return RowOutcome("quarantine", "DD-003", field="Id")
    post_type, type_error = _parse_type(attributes.get("PostTypeId"))
    if type_error:
        return RowOutcome("quarantine", "DD-004", field="PostTypeId", post_id=post_id)
    assert post_id is not None and post_type is not None
    if post_type not in {1, 2}:
        return RowOutcome(
            "skip", "DD-005", post_id=post_id, post_type_id=post_type, detail=str(post_type)
        )
    parent, parent_error = _optional_id(attributes.get("ParentId"))
    if post_type == 2 and "ParentId" not in attributes:
        return RowOutcome("quarantine", "DD-006", field="ParentId", post_id=post_id, post_type_id=2)
    if parent_error:
        return RowOutcome("quarantine", "DD-003", field="ParentId", post_id=post_id)
    if post_type == 1 and post_id not in context.selected:
        return RowOutcome("skip", "DD-007", post_id=post_id, post_type_id=1)
    if post_type == 2 and parent not in context.selected:
        return RowOutcome("skip", "DD-007", post_id=post_id, post_type_id=2, parent_id=parent)
    parsed, date_error = _dates(attributes, context)
    if date_error is not None:
        return RowOutcome(
            "quarantine",
            date_error[0],
            field=date_error[1],
            post_id=post_id,
            post_type_id=post_type,
        )
    body = attributes.get("Body")
    if body is None or body.strip() == "":
        return RowOutcome(
            "quarantine", "DD-011", field="Body", post_id=post_id, post_type_id=post_type
        )
    if len(body) > context.max_body_chars:
        return RowOutcome(
            "quarantine",
            "DD-012",
            field="Body",
            post_id=post_id,
            post_type_id=post_type,
            detail=str(len(body)),
        )
    score, score_error = _score(attributes.get("Score"))
    if score_error:
        return RowOutcome(
            "quarantine", "DD-013", field="Score", post_id=post_id, post_type_id=post_type
        )
    numbers, number_error = _numbers(attributes)
    if number_error is not None:
        return RowOutcome(
            "quarantine",
            number_error[0],
            field=number_error[1],
            post_id=post_id,
            post_type_id=post_type,
        )
    warnings: list[str] = []
    owner = numbers["owner_user_id"]
    if owner is None:
        warnings.append("DD-014")
    license_name = attributes.get("ContentLicense")
    if license_name is None or license_name.strip() == "":
        license_name = None
        warnings.append("DD-015")
    record = _record(
        row_number,
        context,
        post_id,
        post_type,
        parent,
        parsed,
        body,
        score,
        numbers,
        license_name,
        attributes,
    )
    return RowOutcome(
        "emit",
        "",
        record=record,
        post_id=post_id,
        post_type_id=post_type,
        parent_id=parent,
        fingerprint=digest(
            canonical({key: value for key, value in record.items() if key != "source_row_number"})
        ),
        warnings=tuple(warnings),
    )


def content_fields(dump_row: dict[str, Any], api_row: dict[str, Any]) -> tuple[str, ...]:
    """Pure DD-018 comparison. Ingest does not call this and does not read API bronze."""
    if dump_row.get("site") != api_row.get("site") or dump_row.get("post_id") != api_row.get(
        "post_id"
    ):
        raise ValueError("records are not the same post")
    names = ("body", "title", "score", "accepted_answer_id", "last_activity_at")
    return tuple(name for name in names if dump_row.get(name) != api_row.get(name))


def _too_large(value: str, limit: int) -> bool:
    return len(value.encode("utf-8")) > limit


def _parse_id(value: str | None) -> tuple[int | None, bool]:
    if value is None or _INTEGER.fullmatch(value) is None:
        return None, True
    number = int(value)
    if number < 1:
        return None, True
    return number, False


def _parse_type(value: str | None) -> tuple[int | None, bool]:
    if value is None or _INTEGER.fullmatch(value) is None:
        return None, True
    return int(value), False


def _optional_id(value: str | None) -> tuple[int | None, bool]:
    if value is None:
        return None, False
    if _INTEGER.fullmatch(value) is None or int(value) < 1:
        return None, True
    return int(value), False


def _score(value: str | None) -> tuple[int | None, bool]:
    if value is None:
        return None, False
    if _SIGNED.fullmatch(value) is None:
        return None, True
    return int(value), False


def _numbers(
    attributes: dict[str, str],
) -> tuple[dict[str, int | None], tuple[str, str] | None]:
    parsed: dict[str, int | None] = {}
    for source, target in _IDENTIFIERS.items():
        if source == "ParentId":
            continue
        number, error = _optional_id(attributes.get(source))
        if error:
            return {}, ("DD-003", source)
        parsed[target] = number
    for source, target in _COUNTS.items():
        number, error = _count(attributes.get(source))
        if error:
            return {}, ("DD-013", source)
        parsed[target] = number
    return parsed, None


def _count(value: str | None) -> tuple[int | None, bool]:
    if value is None:
        return None, False
    if _INTEGER.fullmatch(value) is None:
        return None, True
    return int(value), False


def _dates(
    attributes: dict[str, str], context: RowContext
) -> tuple[dict[str, str | None], tuple[str, str] | None]:
    parsed: dict[str, str | None] = {}
    for source, target in _DATES.items():
        raw = attributes.get(source)
        if raw is None:
            parsed[target] = None
            continue
        instant = parse_dump_instant(raw)
        if instant is None:
            return {}, ("DD-009", source)
        if source == "CreationDate" and (
            instant < EARLIEST
            or instant > context.observed_at
            or (context.cutoff is not None and instant > context.cutoff)
        ):
            return {}, ("DD-010", source)
        parsed[target] = stamp(instant)
    return parsed, None


def _record(
    row_number: int,
    context: RowContext,
    post_id: int,
    post_type: int,
    parent: int | None,
    parsed: dict[str, str | None],
    body: str,
    score: int | None,
    numbers: dict[str, int | None],
    license_name: str | None,
    attributes: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_VERSION,
        "source": SOURCE_KIND,
        "source_kind": SOURCE_KIND,
        "site": context.site,
        "ingestion_identity": context.ingestion_identity,
        "post_id": post_id,
        "post_type_id": post_type,
        "source_row_number": row_number,
        "source_file_name": context.source_file_name,
        "source_sha256": context.source_sha256,
        "source_file_size": context.source_file_size,
        "dump_publication_id": context.dump_publication_id,
        "parser_version": PARSER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "parent_id": parent,
        "accepted_answer_id": numbers["accepted_answer_id"],
        "source_created_at": parsed["source_created_at"],
        "score": score,
        "body": body,
        "title": attributes.get("Title"),
        "tags_raw": attributes.get("Tags"),
        "owner_user_id": numbers["owner_user_id"],
        "content_license": license_name,
        "last_edit_at": parsed["last_edit_at"],
        "last_activity_at": parsed["last_activity_at"],
        "closed_at": parsed["closed_at"],
        "view_count": numbers["view_count"],
        "answer_count": numbers["answer_count"],
        "comment_count": numbers["comment_count"],
    }
