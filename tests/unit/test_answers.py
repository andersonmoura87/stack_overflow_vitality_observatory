"""Unit coverage for answer batches, Bronze, checkpoint and Silver history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from stackoverflow_vitality.answers.batching import (
    answers_url,
    normalize_question_ids,
    plan_batches,
)
from stackoverflow_vitality.answers.bronze import LocalAnswerBronzeStore
from stackoverflow_vitality.answers.checkpoint import CheckpointConflict, LocalAnswerCheckpoint
from stackoverflow_vitality.answers.collect import collect_answers
from stackoverflow_vitality.answers.config import AnswerCollectionConfig, load_answer_config
from stackoverflow_vitality.answers.history import content_hash, empty_state, observe, rebuild
from stackoverflow_vitality.answers.models import AnswerCheckpoint
from stackoverflow_vitality.answers.quality import normalize_answer
from stackoverflow_vitality.answers.transform import AnswerTransformFailure, transform_answers
from stackoverflow_vitality.cli import main
from stackoverflow_vitality.ingestion.http import HttpxStackExchangeClient
from stackoverflow_vitality.ingestion.interfaces import ApiResponse
from stackoverflow_vitality.storage.files import LocalLockConflict

ENDPOINT = "https://api.stackexchange.com/2.3/questions"
NOW = datetime(2026, 9, 21, tzinfo=UTC)
COLLECTED = "2026-09-10T00:00:00Z"
CREATED = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())


class FakeHttp:
    def __init__(
        self,
        pages: dict[str, list[dict[str, Any]]],
        *,
        fail: set[str] | None = None,
        backoff: int | None = None,
    ) -> None:
        self.pages = pages
        self.fail = fail or set()
        self.backoff = backoff
        self.calls: list[tuple[str, dict[str, str | int]]] = []

    def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse:
        self.calls.append((endpoint, dict(params)))
        if endpoint in self.fail:
            raise RuntimeError("batch failed")
        page = int(params["page"])
        payload = self.pages[endpoint][page - 1]
        items = payload["items"]
        if not isinstance(items, list):
            raise AssertionError("items must be a list")
        quota = payload.get("quota_remaining", 10)
        return ApiResponse(
            items=items,
            has_more=bool(payload["has_more"]),
            quota_remaining=quota if type(quota) is int else None,
            backoff_seconds=self.backoff,
            payload=payload,
        )


def test_batches_are_deterministic_and_reject_bad_ids() -> None:
    assert normalize_question_ids([3, 1, 2]) == (1, 2, 3)
    with pytest.raises(ValueError, match="unique"):
        normalize_question_ids([1, 1])
    with pytest.raises(ValueError, match="positive"):
        normalize_question_ids([0, True, -1])
    with pytest.raises(ValueError, match="nonempty"):
        normalize_question_ids([])
    batches = plan_batches(
        (1, 2, 3, 4), endpoint=ENDPOINT, max_batch_size=2, max_url_length=2000, query_allowance=10
    )
    assert [batch.question_ids for batch in batches] == [(1, 2), (3, 4)]
    assert batches[0].url.endswith("/1;2/answers")
    assert "tagged" not in batches[0].url
    again = plan_batches(
        (4, 3, 2, 1), endpoint=ENDPOINT, max_batch_size=2, max_url_length=2000, query_allowance=10
    )
    assert [batch.batch_id for batch in again] == [batch.batch_id for batch in batches]


def test_url_budget_splits_and_rejects_an_unsplittable_id() -> None:
    batches = plan_batches(
        (10, 11, 12), endpoint=ENDPOINT, max_batch_size=100, max_url_length=55, query_allowance=0
    )
    assert all(len(batch.question_ids) == 1 for batch in batches)
    with pytest.raises(ValueError, match="single batch"):
        plan_batches(
            (10,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=32, query_allowance=0
        )


def test_config_precedence_rejects_secrets(tmp_path: Path) -> None:
    path = tmp_path / "answers.yml"
    path.write_text(
        "site: stackoverflow\nendpoint: https://api.stackexchange.com/2.3/questions\n"
        "max_batch_size: 5\nretry:\n  max_attempts: 2\n",
        encoding="utf-8",
    )
    config = load_answer_config(
        path,
        {"STACKEXCHANGE_ANSWERS_SITE": "superuser"},
        {"max_batch_size": 7, "page_size": None},
    )
    assert config.site == "superuser"
    assert config.max_batch_size == 7
    assert config.max_attempts == 2
    path.write_text("api_key: secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="secret"):
        load_answer_config(path, {}, {})
    path.write_text(
        "site: stackoverflow\n"
        "endpoint: https://api.stackexchange.com/2.3/questions\n"
        "mode: sideways\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mode"):
        load_answer_config(path, {}, {})


def test_collection_writes_immutable_pages_and_advances_once(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(max_batch_size=2, max_pages=2, bronze_output=str(tmp_path))
    batches = plan_batches(
        (10, 20),
        endpoint=config.endpoint,
        max_batch_size=2,
        max_url_length=2000,
        query_allowance=20,
    )
    url = batches[0].url
    http = FakeHttp(
        {
            url: [
                {"items": [_answer(1, 10, "one"), _answer(1, 10, "one")], "has_more": True},
                {"items": [_answer(2, 20, "two")], "has_more": False},
            ]
        }
    )
    sleeps: list[float] = []
    http.backoff = 3
    result = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="incremental",
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
        sleeper=sleeps.append,
    )
    assert result.status == "completed"
    assert result.checkpoint_advanced is True
    assert result.duplicates == 1
    assert sleeps == [3.0, 3.0]
    page = next((tmp_path / "answers").rglob("page=0001.json"))
    stored = json.loads(page.read_text(encoding="utf-8"))
    assert stored["schema_version"] == "bronze_stackexchange_answers_snapshot.v1"
    assert stored["payload"]["items"][0]["body"] == "<p>one</p>"
    assert "key" not in stored["request_params"]
    assert "run=" in str(page)
    assert len(str(page.relative_to(tmp_path))) < 180
    manifest = next((tmp_path / "answers" / "manifests").glob("*.json"))
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "completed"
    replay = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="incremental",
        now=lambda: datetime(2026, 9, 11, tzinfo=UTC),
        sleeper=sleeps.append,
    )
    assert replay.status == "completed"
    assert replay.pages_persisted == 0
    checkpoint = (tmp_path / "answers" / "checkpoint.json").read_text(encoding="utf-8")
    http.pages[url][0] = {"items": [_answer(9, 10, "changed")], "has_more": False}
    conflict = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="refresh",
        now=lambda: datetime(2026, 9, 12, tzinfo=UTC),
    )
    assert conflict.status == "completed"
    assert conflict.checkpoint_advanced is False
    assert conflict.pages_persisted == 1
    assert (tmp_path / "answers" / "checkpoint.json").read_text(encoding="utf-8") == checkpoint
    pages = list((tmp_path / "answers").rglob("page=0001.json"))
    assert len(pages) == 2
    bodies = {
        json.loads(path.read_text(encoding="utf-8"))["payload"]["items"][0]["body"]
        for path in pages
    }
    assert bodies == {"<p>one</p>", "<p>changed</p>"}
    assert {path.parent.parent.name for path in pages} == {
        f"run={result.run_id}",
        f"run={conflict.run_id}",
    }


def test_failed_batch_does_not_advance_checkpoint(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(max_batch_size=1, bronze_output=str(tmp_path))
    batches = plan_batches(
        (10, 20),
        endpoint=config.endpoint,
        max_batch_size=1,
        max_url_length=2000,
        query_allowance=20,
    )
    http = FakeHttp(
        {batches[0].url: [{"items": [_answer(1, 10)], "has_more": False}]},
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
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert result.status == "failed"
    assert result.checkpoint_advanced is False
    assert not (tmp_path / "answers" / "checkpoint.json").exists()
    manifest = json.loads(next((tmp_path / "answers" / "manifests").glob("*.json")).read_text())
    assert manifest["status"] == "failed"


def test_refresh_does_not_move_checkpoint(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(max_batch_size=1, bronze_output=str(tmp_path), mode="refresh")
    batches = plan_batches(
        (10,), endpoint=config.endpoint, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    http = FakeHttp({batches[0].url: [{"items": [], "has_more": False}]})
    result = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="refresh",
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert result.status == "empty"
    assert result.checkpoint_advanced is False
    assert not (tmp_path / "answers" / "checkpoint.json").exists()


def test_truncation_quota_and_lock(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(max_pages=1, bronze_output=str(tmp_path))
    batches = plan_batches(
        (10,), endpoint=config.endpoint, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    http = FakeHttp({batches[0].url: [{"items": [_answer(1, 10)], "has_more": True}]})
    truncated = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path / "trunc"),
        checkpoints=LocalAnswerCheckpoint(tmp_path / "trunc"),
        mode="incremental",
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert truncated.status == "truncated"
    assert truncated.checkpoint_advanced is False
    quota_http = FakeHttp(
        {batches[0].url: [{"items": [_answer(1, 10)], "has_more": True, "quota_remaining": 0}]}
    )
    quota = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=quota_http,
        bronze=LocalAnswerBronzeStore(tmp_path / "quota"),
        checkpoints=LocalAnswerCheckpoint(tmp_path / "quota"),
        mode="incremental",
        max_pages=5,
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert quota.status == "failed"
    lock = tmp_path / "locked" / "answers" / ".writer.lock"
    lock.mkdir(parents=True)
    with pytest.raises(LocalLockConflict):
        collect_answers(
            config,
            batches,
            origin="ids.json",
            run_id=uuid4(),
            http=http,
            bronze=LocalAnswerBronzeStore(tmp_path / "locked"),
            checkpoints=LocalAnswerCheckpoint(tmp_path / "locked"),
            mode="incremental",
            now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
        )


def test_checkpoint_is_monotonic(tmp_path: Path) -> None:
    store = LocalAnswerCheckpoint(tmp_path)
    moment = datetime(2026, 9, 10, tzinfo=UTC)
    first = AnswerCheckpoint(
        "stackexchange_api", "stackoverflow", ENDPOINT, "question_id_batches", ("a",), (1,), moment
    )
    store.update(None, first)
    second = AnswerCheckpoint(
        "stackexchange_api",
        "stackoverflow",
        ENDPOINT,
        "question_id_batches",
        ("a", "b"),
        (1, 2),
        moment,
    )
    store.update(first, second)
    with pytest.raises(CheckpointConflict):
        store.update(first, second)


def test_http_client_retries_answer_url_without_logging_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        assert "/questions/10;20/answers" in str(request.url)
        assert "tagged=" not in str(request.url)
        if calls["count"] == 1:
            return httpx.Response(429, json={"backoff": 1})
        return httpx.Response(200, json={"items": [], "has_more": False, "quota_remaining": 9})

    sleeps: list[float] = []
    client = HttpxStackExchangeClient(
        api_key="super-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=2,
        initial_backoff_seconds=1,
        max_backoff_seconds=5,
        sleeper=sleeps.append,
        jitter=lambda _base: 0,
    )
    with caplog.at_level("INFO"):
        response = client.get(
            answers_url(ENDPOINT, (10, 20)),
            {"site": "stackoverflow", "page": 1, "pagesize": 1, "filter": "withbody"},
        )
    assert response.quota_remaining == 9
    assert sleeps
    assert "super-secret" not in caplog.text
    client.close()


def test_failed_run_does_not_count_as_checkpoint_progress(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(max_batch_size=1, bronze_output=str(tmp_path))
    batches = plan_batches(
        (10, 20),
        endpoint=config.endpoint,
        max_batch_size=1,
        max_url_length=2000,
        query_allowance=20,
    )
    failed_http = FakeHttp(
        {batches[0].url: [{"items": [_answer(1, 10, "first")], "has_more": False}]},
        fail={batches[1].url},
    )
    failed = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=failed_http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="incremental",
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert failed.status == "failed"
    assert not (tmp_path / "answers" / "checkpoint.json").exists()
    retry_http = FakeHttp(
        {
            batches[0].url: [{"items": [_answer(1, 10, "again")], "has_more": False}],
            batches[1].url: [{"items": [_answer(2, 20, "second")], "has_more": False}],
        }
    )
    retried = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=retry_http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="incremental",
        now=lambda: datetime(2026, 9, 11, tzinfo=UTC),
    )
    assert retried.status == "completed"
    assert retried.checkpoint_advanced is True
    assert len(retry_http.calls) == 2
    assert len(list((tmp_path / "answers").rglob("page=0001.json"))) == 3


def test_same_run_replay_is_idempotent(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(bronze_output=str(tmp_path))
    batches = plan_batches(
        (10,), endpoint=config.endpoint, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    http = FakeHttp({batches[0].url: [{"items": [_answer(1, 10)], "has_more": False}]})
    run_id = uuid4()
    moment = datetime(2026, 9, 10, tzinfo=UTC)
    kwargs = {
        "origin": "ids.json",
        "run_id": run_id,
        "http": http,
        "bronze": LocalAnswerBronzeStore(tmp_path),
        "checkpoints": LocalAnswerCheckpoint(tmp_path),
        "mode": "refresh",
        "now": lambda: moment,
    }
    first = collect_answers(config, batches, **kwargs)
    second = collect_answers(config, batches, **kwargs)
    assert first.status == second.status == "completed"
    assert second.pages_persisted == 1
    assert second.checkpoint_advanced is False
    assert len(list((tmp_path / "answers").rglob("page=0001.json"))) == 1
    assert not (tmp_path / "answers" / "checkpoint.json").exists()


def test_transform_rejects_drift_and_checksum_before_publish(tmp_path: Path) -> None:
    bronze, silver, quarantine = tmp_path / "bronze", tmp_path / "silver", tmp_path / "quarantine"
    for path in (bronze, silver, quarantine):
        path.mkdir()
    config = AnswerCollectionConfig(bronze_output=str(bronze))
    batches = plan_batches(
        (10,), endpoint=config.endpoint, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=FakeHttp({batches[0].url: [{"items": [_answer(1, 10)], "has_more": False}]}),
        bronze=LocalAnswerBronzeStore(bronze),
        checkpoints=LocalAnswerCheckpoint(bronze),
        mode="incremental",
        now=lambda: datetime(2026, 9, 10, tzinfo=UTC),
    )
    page = next(bronze.rglob("page=0001.json"))
    drifted = json.loads(page.read_text(encoding="utf-8"))
    drifted["schema_version"] = "bronze_stackexchange_answers_snapshot.v0"
    page.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(AnswerTransformFailure, match="schema"):
        transform_answers(bronze, silver, quarantine)
    assert list(silver.rglob("*.jsonl")) == []
    drifted["schema_version"] = "bronze_stackexchange_answers_snapshot.v1"
    drifted["checksum"] = "0" * 64
    page.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(AnswerTransformFailure, match="checksum"):
        transform_answers(bronze, silver, quarantine)
    assert list(silver.rglob("*.jsonl")) == []


def test_last_activity_stays_on_the_observation(tmp_path: Path) -> None:
    del tmp_path
    opening = _record(1, body="<p>a</p>", collected="2026-09-11T00:00:00Z", score=1, accepted=False)
    opening["last_activity_at"] = "2026-09-11T00:00:00Z"
    later = _record(
        1,
        body="<p>a</p>",
        collected="2026-09-12T00:00:00Z",
        score=8,
        accepted=True,
        index=1,
        page=2,
    )
    later["last_activity_at"] = "2026-09-12T03:00:00Z"
    state = empty_state()
    observe(state, opening)
    observe(state, later)
    rebuild(state)
    versions = state["versions"]
    assert len(versions) == 1
    assert "last_activity_at" not in versions[0]
    assert content_hash(opening) == content_hash(later)
    observed = sorted(item["last_activity_at"] for item in state["observations"].values())
    assert observed == ["2026-09-11T00:00:00Z", "2026-09-12T03:00:00Z"]
    assert {item["score"] for item in state["observations"].values()} == {1, 8}


def test_history_is_independent_of_input_order() -> None:
    early = _record(1, body="<p>a</p>", collected="2026-09-11T00:00:00Z", score=1, accepted=False)
    edited = _record(
        1,
        body="<p>b</p><code>x=1</code>",
        collected="2026-09-12T00:00:00Z",
        score=4,
        accepted=True,
        index=1,
        page=2,
    )
    score_only = _record(
        1,
        body="<p>b</p><code>x=1</code>",
        collected="2026-09-13T00:00:00Z",
        score=9,
        accepted=False,
        index=2,
        page=3,
    )
    forward = _rebuild([early, edited, score_only])
    backward = _rebuild([score_only, early, edited])
    assert forward == backward
    assert [item["body_html"] for item in forward] == ["<p>a</p>", "<p>b</p><code>x=1</code>"]
    assert forward[0]["valid_to"] == "2026-09-12T00:00:00Z"
    assert forward[-1]["is_current"] is True
    assert content_hash(edited) == content_hash(score_only)
    assert content_hash(early) != content_hash(edited)
    state = empty_state()
    assert observe(state, early) is True
    assert observe(state, early) is False
    conflict = dict(early)
    conflict["body_html"] = "<p>nope</p>"
    with pytest.raises(ValueError, match="conflict"):
        observe(state, conflict)


def test_quality_keeps_nulls_and_quarantines_bad_rows() -> None:
    envelope = _envelope((10,))
    missing_owner, events = normalize_answer(
        _answer(1, 10, owner=None, score=None, accepted=None),
        envelope,
        0,
        now=NOW,
        max_body_chars=1000,
    )
    assert missing_owner is not None
    assert missing_owner["score"] is None
    assert missing_owner["is_accepted"] is None
    assert missing_owner["owner_user_id"] is None
    assert missing_owner["last_activity_at"] is None
    assert events[0].rule_id == "SA-010"
    outside, outside_events = normalize_answer(
        _answer(2, 99), envelope, 1, now=NOW, max_body_chars=1000
    )
    assert outside is None
    assert outside_events[0].rule_id == "SA-003"
    late, late_events = normalize_answer(
        _answer(3, 10, created=int(datetime(2026, 9, 10, 12, tzinfo=UTC).timestamp())),
        envelope,
        2,
        now=NOW,
        max_body_chars=1000,
    )
    assert late is None
    assert any(event.rule_id == "SA-005" for event in late_events)


def test_dry_runs_do_not_touch_network_or_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = tmp_path / "ids.json"
    ids.write_text("[20, 10, 30]", encoding="utf-8")
    config = tmp_path / "answers.yml"
    config.write_text(
        "site: stackoverflow\nendpoint: https://api.stackexchange.com/2.3/questions\n"
        f"bronze_output: {tmp_path / 'bronze'}\nsilver_output: {tmp_path / 'silver'}\n",
        encoding="utf-8",
    )

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network client must not be constructed")

    monkeypatch.setattr("stackoverflow_vitality.answers.cli.HttpxStackExchangeClient", explode)
    code = main(
        [
            "collect-answers",
            "--config",
            str(config),
            "--question-ids-file",
            str(ids),
            "--dry-run",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["batches"] == 1
    assert payload["question_ids"] == 3
    assert not (tmp_path / "bronze").exists()
    for path in (tmp_path / "bronze", tmp_path / "silver", tmp_path / "quarantine"):
        path.mkdir()
    code = main(
        [
            "transform-answers",
            "--bronze-path",
            str(tmp_path / "bronze"),
            "--silver-path",
            str(tmp_path / "silver"),
            "--quarantine-path",
            str(tmp_path / "quarantine"),
            "--dry-run",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    assert list((tmp_path / "silver").rglob("*.jsonl")) == []


def _answer(
    answer_id: int,
    question_id: int,
    body: str = "body",
    *,
    owner: int | None = 7,
    score: int | None = 1,
    accepted: bool | None = False,
    created: int = CREATED,
) -> dict[str, object]:
    item: dict[str, object] = {
        "answer_id": answer_id,
        "question_id": question_id,
        "creation_date": created,
        "body": f"<p>{body}</p>",
        "content_license": "CC BY-SA 4.0",
    }
    if owner is not None:
        item["owner"] = {"user_id": owner}
    if score is not None:
        item["score"] = score
    if accepted is not None:
        item["is_accepted"] = accepted
    return item


def _envelope(question_ids: tuple[int, ...]) -> dict[str, object]:
    return {
        "run_id": str(uuid4()),
        "page": 1,
        "checksum": "a" * 64,
        "site": "stackoverflow",
        "recovered_at": COLLECTED,
        "question_ids": list(question_ids),
        "batch_id": "b" * 64,
    }


def _record(
    answer_id: int,
    *,
    body: str,
    collected: str,
    score: int,
    accepted: bool,
    index: int = 0,
    page: int = 1,
) -> dict[str, object]:
    text_blocks = ("x=1",) if "<code>" in body else ()
    return {
        "schema_version": "silver_answer_content_versions.v1",
        "entity_type": "answer",
        "site": "stackoverflow",
        "answer_id": answer_id,
        "question_id": 10,
        "creation_at": "2026-09-01T00:00:00Z",
        "last_edit_at": None,
        "last_activity_at": None,
        "owner_user_id": 7,
        "body_html": body,
        "body_text": "text",
        "code_blocks": list(text_blocks),
        "content_license": "CC BY-SA 4.0",
        "collected_at": collected,
        "score": score,
        "is_accepted": accepted,
        "source_run_id": "run",
        "source_page": page,
        "source_batch_id": "batch",
        "source_file_checksum": "c" * 64,
        "source_item_index": index,
        "warnings": [],
    }


def _rebuild(records: list[dict[str, object]]) -> list[dict[str, object]]:
    state = empty_state()
    for record in records:
        observe(state, record)
    rebuild(state)
    return list(state["versions"])
