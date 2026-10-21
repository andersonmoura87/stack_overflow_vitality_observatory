"""Content addressed identity for one Data Dump Silver generation."""

from __future__ import annotations

import hashlib
from typing import Any

from stackoverflow_vitality.storage.files import canonical

TRANSFORMER_VERSION = "1.0.0"
BODY_RULES = "content_parser.v1"
TAG_RULES = "angle_brackets_sorted_unique.v1"
QUESTION_CONTENT_SCHEMA = "silver_data_dump_question_content.v1"
QUESTION_OBSERVATIONS_SCHEMA = "silver_data_dump_question_observations.v1"
ANSWER_CONTENT_SCHEMA = "silver_data_dump_answer_content.v1"
ANSWER_OBSERVATIONS_SCHEMA = "silver_data_dump_answer_observations.v1"
MANIFEST_SCHEMA = "silver_data_dump_manifest.v1"
QUALITY_SCHEMA = "silver_data_dump_quality.v1"

QUESTION_CONTENT_HASH_FIELDS = (
    "site",
    "question_id",
    "title",
    "body_html",
    "body_text",
    "code_blocks",
    "tags",
    "tags_raw",
    "owner_user_id",
    "content_license",
    "source_created_at",
    "last_edit_at",
    "closed_at",
)
ANSWER_CONTENT_HASH_FIELDS = (
    "site",
    "answer_id",
    "question_id",
    "body_html",
    "body_text",
    "code_blocks",
    "owner_user_id",
    "content_license",
    "source_created_at",
    "last_edit_at",
)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def content_hash(fields: dict[str, Any]) -> str:
    return digest(canonical(fields))


def silver_generation_id(*, ingestion_identity: str, max_body_chars: int) -> str:
    payload = canonical(
        {
            "ingestion_identity": ingestion_identity,
            "contracts": {
                "question_content": QUESTION_CONTENT_SCHEMA,
                "question_observations": QUESTION_OBSERVATIONS_SCHEMA,
                "answer_content": ANSWER_CONTENT_SCHEMA,
                "answer_observations": ANSWER_OBSERVATIONS_SCHEMA,
            },
            "transformer_version": TRANSFORMER_VERSION,
            "body_rules": BODY_RULES,
            "tag_rules": TAG_RULES,
            "max_body_chars": max_body_chars,
        }
    )
    return digest(payload)
