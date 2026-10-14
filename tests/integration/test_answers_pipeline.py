"""Simulated HTTP to immutable Bronze to historical Silver."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from stackoverflow_vitality.answers.batching import plan_batches
from stackoverflow_vitality.answers.bronze import LocalAnswerBronzeStore
from stackoverflow_vitality.answers.checkpoint import LocalAnswerCheckpoint
from stackoverflow_vitality.answers.collect import collect_answers
from stackoverflow_vitality.answers.config import AnswerCollectionConfig
from stackoverflow_vitality.answers.transform import transform_answers
from stackoverflow_vitality.ingestion.interfaces import ApiResponse

ENDPOINT = "https://api.stackexchange.com/2.3/questions"
CREATED = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
T0 = datetime(2026, 9, 10, tzinfo=UTC)
T1 = datetime(2026, 9, 11, tzinfo=UTC)
T2 = datetime(2026, 9, 12, tzinfo=UTC)
T3 = datetime(2026, 9, 13, tzinfo=UTC)


class ScriptedHttp:
    def __init__(
        self, pages: dict[str, list[dict[str, object]]], fail: set[str] | None = None
    ) -> None:
        self.pages = pages
        self.fail = fail or set()

    def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse:
        if endpoint in self.fail:
            raise RuntimeError("partial batch")
        payload = self.pages[endpoint][int(params["page"]) - 1]
        items = payload["items"]
        assert isinstance(items, list)
        return ApiResponse(
            items=items,
            has_more=bool(payload["has_more"]),
            quota_remaining=8,
            payload=payload,
        )


def test_partial_batch_is_not_global_success(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(max_batch_size=1, bronze_output=str(tmp_path))
    batches = plan_batches(
        (10, 20), endpoint=ENDPOINT, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    http = ScriptedHttp(
        {batches[0].url: [{"items": [_item(1, 10, "<p>ok</p>")], "has_more": False}]},
        fail={batches[1].url},
    )
    result = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="incremental",
        now=lambda: T0,
    )
    assert result.status == "failed"
    assert result.checkpoint_advanced is False
    manifest = json.loads(next((tmp_path / "answers" / "manifests").glob("*.json")).read_text())
    assert manifest["status"] == "failed"
    assert manifest["question_ids_pending"] == [20]


def test_simulated_http_bronze_and_silver_history(tmp_path: Path) -> None:
    bronze, silver, quarantine = tmp_path / "bronze", tmp_path / "silver", tmp_path / "quarantine"
    for path in (bronze, silver, quarantine):
        path.mkdir()
    config = AnswerCollectionConfig(max_pages=3, bronze_output=str(bronze))
    batches = plan_batches(
        (10, 20), endpoint=ENDPOINT, max_batch_size=20, max_url_length=2000, query_allowance=20
    )
    original = _item(100, 10, "<p>Original</p>", score=1, accepted=False, owner=4)
    edited = _item(
        100,
        10,
        "<p>Edited</p><pre><code>print(1)</code></pre>",
        score=5,
        accepted=True,
        owner=4,
    )
    also_accepted = _item(101, 10, "<p>Other</p>", score=0, accepted=True, owner=8)
    missing_owner = _item(200, 20, "<p>Anon</p>", score=2, accepted=True, owner=None)
    outside = _item(300, 99, "<p>Outside</p>")
    invalid = {"body": "<p>no id</p>"}
    http = ScriptedHttp(
        {
            batches[0].url: [
                {"items": [edited, also_accepted], "has_more": True},
                {"items": [original, missing_owner, outside, invalid], "has_more": False},
            ]
        }
    )
    times = [T0, T2, T2, T1, T1]

    def now() -> datetime:
        return times.pop(0) if times else T3

    collected = collect_answers(
        config,
        batches,
        origin="questions.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(bronze),
        checkpoints=LocalAnswerCheckpoint(bronze),
        mode="incremental",
        now=now,
    )
    assert collected.status == "completed"
    assert collected.duplicates == 1
    assert collected.outside_batch == 1
    assert collected.checkpoint_advanced is True
    questions = silver / "questions.jsonl"
    questions.write_text(
        json.dumps(
            {
                "site": "stackoverflow",
                "question_id": 10,
                "is_current": True,
                "accepted_answer_id": 999,
            }
        )
        + "\n"
        + json.dumps(
            {
                "site": "stackoverflow",
                "question_id": 20,
                "is_current": True,
                "accepted_answer_id": 200,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    first = transform_answers(
        bronze,
        silver,
        quarantine,
        questions_silver=questions,
        transformation_run_id="answers-run",
        now=lambda: datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert first.status == "partial"
    assert first.reconciliation == "compared"
    replay = transform_answers(
        bronze,
        silver,
        quarantine,
        questions_silver=questions,
        transformation_run_id="answers-run",
        now=lambda: datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert replay.observations == first.observations
    versions = [
        json.loads(line)
        for line in (next((silver / "answers").rglob("content_versions.jsonl")))
        .read_text()
        .splitlines()
    ]
    answer_100 = [row for row in versions if row["answer_id"] == 100]
    assert [row["body_html"] for row in answer_100] == [
        "<p>Original</p>",
        "<p>Edited</p><pre><code>print(1)</code></pre>",
    ]
    assert answer_100[0]["valid_from"] == "2026-09-11T00:00:00Z"
    assert answer_100[1]["valid_from"] == "2026-09-12T00:00:00Z"
    assert any(row["code_blocks"] == ["print(1)"] for row in answer_100)
    observations = [
        json.loads(line)
        for line in next((silver / "answers").rglob("observations.jsonl")).read_text().splitlines()
    ]
    rows = sorted(
        (row for row in observations if row["answer_id"] == 100),
        key=lambda row: row["collected_at"],
    )
    assert [row["score"] for row in rows] == [1, 5]
    assert [row["is_accepted"] for row in rows] == [False, True]
    anon = next(row for row in versions if row["answer_id"] == 200)
    assert anon["owner_user_id"] is None
    rules = {
        json.loads(line)["rule_id"]
        for line in (quarantine / "answers" / "transformation_run_id=answers-run" / "items.jsonl")
        .read_text()
        .splitlines()
    }
    assert "SA-001" in rules
    assert "SA-003" in rules
    reconciliation = {
        json.loads(line)["rule_id"]
        for line in (
            quarantine / "answers" / "transformation_run_id=answers-run" / "reconciliation.jsonl"
        )
        .read_text()
        .splitlines()
    }
    assert {"SA-011", "SA-012", "SA-013"} <= reconciliation
    assert not (silver / "questions").exists()


def _item(
    answer_id: int,
    question_id: int,
    body: str,
    *,
    score: int = 1,
    accepted: bool = False,
    owner: int | None = 4,
) -> dict[str, object]:
    item: dict[str, object] = {
        "answer_id": answer_id,
        "question_id": question_id,
        "creation_date": CREATED,
        "body": body,
        "score": score,
        "is_accepted": accepted,
        "content_license": "CC BY-SA 4.0",
    }
    if owner is not None:
        item["owner"] = {"user_id": owner}
    return item
