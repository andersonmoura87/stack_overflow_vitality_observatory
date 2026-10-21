"""Offline contract of one synthetic Data Dump Silver generation."""

import hashlib
import json
from pathlib import Path

from stackoverflow_vitality.data_dump_silver.transform import transform_data_dump
from stackoverflow_vitality.storage.files import canonical
from tests.dump_fixtures import answer, question
from tests.dump_silver import publish_bronze

QUESTION_CONTENT = {
    "schema_version",
    "source_kind",
    "site",
    "question_id",
    "content_hash",
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
}
QUESTION_OBSERVATIONS = {
    "schema_version",
    "source_kind",
    "site",
    "question_id",
    "content_hash",
    "score",
    "view_count",
    "answer_count",
    "comment_count",
    "accepted_answer_id",
    "last_activity_at",
    "dump_publication_id",
    "dump_cutoff",
    "dump_cutoff_status",
    "source_file_observed_at",
    "source_identity",
    "ingestion_identity",
    "source_row_number",
}
ANSWER_CONTENT = {
    "schema_version",
    "source_kind",
    "site",
    "answer_id",
    "question_id",
    "content_hash",
    "body_html",
    "body_text",
    "code_blocks",
    "owner_user_id",
    "content_license",
    "source_created_at",
    "last_edit_at",
}
ANSWER_OBSERVATIONS = {
    "schema_version",
    "source_kind",
    "site",
    "answer_id",
    "question_id",
    "content_hash",
    "score",
    "comment_count",
    "last_activity_at",
    "is_accepted_in_snapshot",
    "dump_publication_id",
    "dump_cutoff",
    "dump_cutoff_status",
    "source_file_observed_at",
    "source_identity",
    "ingestion_identity",
    "source_row_number",
}
CONTENT_HASH_FIELDS = (
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


def test_published_generation_matches_the_four_contracts(tmp_path: Path) -> None:
    manifest, bronze = publish_bronze(tmp_path, [question(1), answer(8, 1)], (1,))
    output = tmp_path / "silver"
    result = transform_data_dump(
        manifest, output, transformation_run_id="contract-run", max_body_chars=500_000
    )
    assert result["status"] == "completed"
    assert result["consumable"] is True
    generation = output / "g={}".format(result["silver_generation_id"])
    question_content = _lines(generation / "question_content.jsonl")
    question_observations = _lines(generation / "question_observations.jsonl")
    answer_content = _lines(generation / "answer_content.jsonl")
    answer_observations = _lines(generation / "answer_observations.jsonl")
    assert set(question_content[0]) == QUESTION_CONTENT
    assert set(question_observations[0]) == QUESTION_OBSERVATIONS
    assert set(answer_content[0]) == ANSWER_CONTENT
    assert set(answer_observations[0]) == ANSWER_OBSERVATIONS
    content = question_content[0]
    digest = hashlib.sha256(
        canonical({key: content[key] for key in CONTENT_HASH_FIELDS})
    ).hexdigest()
    assert content["content_hash"] == digest
    assert question_observations[0]["content_hash"] == digest
    assert content["body_html"] == "Question body"
    assert content["tags_raw"] == "<python><xml>"
    assert content["tags"] == ["python", "xml"]
    assert "tags" not in answer_content[0]
    assert answer_observations[0]["is_accepted_in_snapshot"] is True
    identity = hashlib.sha256(
        canonical(
            {
                "body_rules": "content_parser.v1",
                "contracts": {
                    "answer_content": "silver_data_dump_answer_content.v1",
                    "answer_observations": "silver_data_dump_answer_observations.v1",
                    "question_content": "silver_data_dump_question_content.v1",
                    "question_observations": "silver_data_dump_question_observations.v1",
                },
                "ingestion_identity": bronze["ingestion_identity"],
                "max_body_chars": 500_000,
                "tag_rules": "angle_brackets_sorted_unique.v1",
                "transformer_version": "1.0.0",
            }
        )
    ).hexdigest()
    assert result["silver_generation_id"] == identity
    stored = json.loads(
        (output / "manifests" / f"{result['silver_generation_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["schema_version"] == "silver_data_dump_manifest.v1"
    assert stored["readable"] is True
    assert stored["consumable"] is True
    assert "replay" not in stored
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in generation.glob("*.jsonl"))
    assert "valid_from" not in serialized
    assert "is_current" not in serialized
    assert "accepted_at" not in serialized
    assert str(tmp_path) not in serialized


def _lines(path: Path) -> list[dict[str, object]]:
    payload = path.read_bytes()
    assert payload.endswith(b"\n")
    rows = [json.loads(line) for line in payload.splitlines()]
    assert payload == b"".join(canonical(row) + b"\n" for row in rows)
    return rows
