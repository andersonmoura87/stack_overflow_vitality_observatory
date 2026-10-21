"""Pure derivations and the Data Dump Silver identity."""

import hashlib

from stackoverflow_vitality.data_dump_silver.derive import derive_body, derive_tags
from stackoverflow_vitality.data_dump_silver.identity import silver_generation_id
from stackoverflow_vitality.storage.files import canonical


def test_tags_are_unique_and_sorted() -> None:
    assert derive_tags("<python><pandas><python>") == ["pandas", "python"]
    assert derive_tags(None) == []
    assert derive_tags("") == []
    assert derive_tags("python") == []


def test_body_keeps_html_and_collects_code_in_order() -> None:
    text, blocks = derive_body("<p>Hello</p><pre><code>x = 1</code></pre><code>y</code>")
    assert text == "Hello"
    assert blocks == ["x = 1", "y"]
    assert derive_body(None) == (None, None)


def test_generation_id_changes_only_with_semantic_input() -> None:
    first = silver_generation_id(ingestion_identity="a" * 64, max_body_chars=500_000)
    same = silver_generation_id(ingestion_identity="a" * 64, max_body_chars=500_000)
    other_limit = silver_generation_id(ingestion_identity="a" * 64, max_body_chars=20)
    other_bronze = silver_generation_id(ingestion_identity="b" * 64, max_body_chars=500_000)
    assert first == same
    assert first != other_limit
    assert first != other_bronze
    payload = canonical(
        {
            "body_rules": "content_parser.v1",
            "contracts": {
                "answer_content": "silver_data_dump_answer_content.v1",
                "answer_observations": "silver_data_dump_answer_observations.v1",
                "question_content": "silver_data_dump_question_content.v1",
                "question_observations": "silver_data_dump_question_observations.v1",
            },
            "ingestion_identity": "a" * 64,
            "max_body_chars": 500_000,
            "tag_rules": "angle_brackets_sorted_unique.v1",
            "transformer_version": "1.0.0",
        }
    )
    assert first == hashlib.sha256(payload).hexdigest()
