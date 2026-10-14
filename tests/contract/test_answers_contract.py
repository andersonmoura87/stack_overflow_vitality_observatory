"""Executable contracts for answer Bronze, Silver and quality records."""

from __future__ import annotations

import json
import math
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


class OnePage:
    def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse:
        assert "tagged" not in endpoint
        item = {
            "answer_id": 7,
            "question_id": 10,
            "creation_date": CREATED,
            "last_edit_date": CREATED,
            "last_activity_date": CREATED,
            "score": None,
            "is_accepted": None,
            "owner": {"user_id": 4},
            "body": "<p>Contract</p><code>y=2</code>",
            "content_license": "CC BY-SA 4.0",
        }
        payload = {"items": [item], "has_more": False}
        return ApiResponse(items=[item], has_more=False, quota_remaining=3, payload=payload)


def test_answer_contracts_keep_nulls_utc_and_lineage(tmp_path: Path) -> None:
    bronze = tmp_path / "bronze"
    silver = tmp_path / "silver"
    quarantine = tmp_path / "quarantine"
    for path in (bronze, silver, quarantine):
        path.mkdir()
    config = AnswerCollectionConfig(bronze_output=str(bronze))
    batches = plan_batches(
        (10,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=OnePage(),
        bronze=LocalAnswerBronzeStore(bronze),
        checkpoints=LocalAnswerCheckpoint(bronze),
        mode="incremental",
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    result = transform_answers(
        bronze,
        silver,
        quarantine,
        transformation_run_id="contract-run",
        now=lambda: datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert result.status == "completed"
    version = _line(silver, "content_versions.jsonl")
    observation = _line(silver, "observations.jsonl")
    catalog = _line(silver, "catalog.jsonl")
    manifest = json.loads(next((silver / "answers" / "manifests").glob("*.json")).read_text())
    quality = _line(quarantine, "items.jsonl")
    assert version["schema_version"] == "silver_answer_content_versions.v1"
    assert version["entity_type"] == "answer"
    assert version["valid_from"].endswith("Z")
    assert version["valid_to"] is None and version["is_current"] is True
    assert version["code_blocks"] == ["y=2"]
    assert observation["schema_version"] == "silver_answer_metric_observations.v1"
    assert observation["score"] is None
    assert observation["is_accepted"] is None
    assert observation["last_activity_at"] == "2026-09-01T00:00:00Z"
    assert "last_activity_at" not in version
    assert observation["record_hash"] == version["record_hash"] == catalog["record_hash"]
    assert catalog["schema_version"] == "silver_answer_catalog.v1"
    assert catalog["entity_type"] == "answer"
    assert manifest["schema_version"] == "silver_answer_transformation.v1"
    assert quality == {} or quality["schema_version"] == "silver_answer_quality.v1"
    _no_nan(version)
    _no_nan(observation)
    bronze_page = json.loads(next(bronze.rglob("page=0001.json")).read_text(encoding="utf-8"))
    assert bronze_page["schema_version"] == "bronze_stackexchange_answers_snapshot.v1"
    assert bronze_page["question_ids"] == [10]
    assert bronze_page["recovered_at"].endswith("Z")
    assert "b=" in str(next(bronze.rglob("page=0001.json")))
    assert "run=" in str(next(bronze.rglob("page=0001.json")))
    assert "10;" not in str(next(bronze.rglob("page=0001.json")))


def _line(root: Path, name: str) -> dict[str, object]:
    path = next(root.rglob(name))
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    loaded = json.loads(text.splitlines()[0])
    assert isinstance(loaded, dict)
    return loaded


def _no_nan(record: dict[str, object]) -> None:
    raw = json.dumps(record)
    assert "NaN" not in raw and "Infinity" not in raw
    assert not any(
        isinstance(value, float) and not math.isfinite(value) for value in record.values()
    )
