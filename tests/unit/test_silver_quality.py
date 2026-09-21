from dataclasses import replace
from datetime import UTC, timedelta

import pytest

from stackoverflow_vitality.silver.content import clean_body, clean_title
from stackoverflow_vitality.silver.quality import QualityConfig, normalize
from tests.silver_fixtures import NOW, TRANSFORM_ID, input_page, normalized, page, question


def test_valid_question_preserves_html_utc_lineage_and_signed_score():
    result = normalized()
    assert result.body_html == question()["body"]
    assert result.body_text == "Hello & world."
    assert result.code_blocks == ("x = 1\n  print(x)\n",)
    assert result.title_clean == "Synthetic & question"
    assert result.tags == ("python", "testing")
    assert result.score == -2
    assert result.creation_at.tzinfo == UTC
    assert result.source_page == 1
    assert result.source_checksum == page().checksum
    assert result.accepted_answer_id is None


@pytest.mark.parametrize(
    "html,text,blocks",
    [
        (None, None, None),
        ("", "", ()),
        ("<p>A  \n B</p><p>C</p>", "A B C", ()),
        ("A<script>secret()</script><style>body{}</style>B", "A B", ()),
        ("<p>broken <b>markup", "broken markup", ()),
        ("<code>a\n b</code> and <code>&lt;x&gt;<br>c</code>", "and", ("a\n b", "<x>\nc")),
        ("<code>unterminated\n", "", ("unterminated\n",)),
        ("&lt;tag&gt; &amp; &#65;", "<tag> & A", ()),
    ],
)
def test_content_parser(html, text, blocks):
    assert clean_body(html) == (text, blocks)


def test_title_entities_and_null():
    assert clean_title(None) is None
    assert clean_title(" &lt;word&gt;\n&nbsp;test ") == "<word> test"


@pytest.mark.parametrize(
    "change,rule",
    [
        ({"question_id": None}, "SQ-002"),
        ({"question_id": True}, "SQ-002"),
        ({"creation_date": None}, "SQ-004"),
        ({"creation_date": "yesterday"}, "SQ-004"),
        ({"creation_date": 0}, "SQ-004"),
        ({"creation_date": 10**30}, "SQ-004"),
        ({"creation_date": 2000000000}, "SQ-004"),
        ({"last_activity_date": 1788134400}, "SQ-005"),
        ({"tags": "python"}, "SQ-008"),
        ({"tags": ["python", 7]}, "SQ-008"),
        ({"tags": [""]}, "SQ-008"),
        ({"view_count": -1}, "SQ-006"),
        ({"answer_count": -1}, "SQ-006"),
        ({"answer_count": True}, "SQ-006"),
        ({"title": 1}, "SQ-009"),
        ({"body": {}}, "SQ-009"),
        ({"owner": []}, "SQ-007"),
        ({"is_answered": 1}, "SQ-012"),
    ],
)
def test_invalid_record_is_not_published(change, rule):
    source = input_page(page([question(**change)]))
    accepted, events = normalize(source.items[0], source, 0, TRANSFORM_ID, NOW, QualityConfig())
    assert accepted is None
    assert rule in {event.rule_id for event in events if event.severity == "error"}


def test_nonobject_payload_quarantined():
    source = input_page(page(["not an object"]))
    accepted, events = normalize(source.items[0], source, 0, TRANSFORM_ID, NOW, QualityConfig())
    assert accepted is None and events[0].rule_id == "SQ-001"


@pytest.mark.parametrize("field", ["run_id", "page", "checksum", "site", "recovered_at"])
def test_missing_lineage_rejected(field):
    source = input_page()
    metadata = dict(source.envelope)
    metadata.pop(field)
    source = replace(source, envelope=metadata)
    accepted, events = normalize(source.items[0], source, 0, TRANSFORM_ID, NOW, QualityConfig())
    assert accepted is None
    assert "SQ-003" in {event.rule_id for event in events}


def test_body_limit_never_exposes_body():
    secret_body = "sensitive-body" * 10
    source = input_page(page([question(body=secret_body)]))
    accepted, events = normalize(
        source.items[0], source, 0, TRANSFORM_ID, NOW, QualityConfig(max_body_chars=5)
    )
    assert accepted is None
    assert secret_body not in str(events)
    assert any(event.rule_id == "SQ-010" for event in events)


def test_warning_publishes_and_legitimate_nulls_stay_null():
    source = input_page(
        page(
            [
                question(
                    body=None,
                    tags=None,
                    score=None,
                    view_count=None,
                    answer_count=0,
                    accepted_answer_id=9,
                )
            ]
        )
    )
    accepted, events = normalize(source.items[0], source, 0, TRANSFORM_ID, NOW, QualityConfig())
    assert accepted is not None
    assert accepted.body_text is None and accepted.code_blocks is None
    assert accepted.view_count is None and accepted.score is None and accepted.tags is None
    assert {event.severity for event in events} == {"warning", "informational"}


def test_false_is_answered_with_answers_is_not_inherently_inconsistent():
    source = input_page(page([question(is_answered=False)]))
    accepted, events = normalize(source.items[0], source, 0, TRANSFORM_ID, NOW, QualityConfig())
    assert accepted is not None and not events


def test_future_collection_time_rejected():
    source = input_page(page(collected=NOW + timedelta(days=2)))
    accepted, events = normalize(source.items[0], source, 0, TRANSFORM_ID, NOW, QualityConfig())
    assert accepted is None
    assert "SQ-003" in {event.rule_id for event in events}
