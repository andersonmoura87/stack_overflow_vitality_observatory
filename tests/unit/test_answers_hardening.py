"""Failure and invariant tests for answer collection. No network."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from stackoverflow_vitality.answers.batching import (
    AnswerBatch,
    answers_url,
    batch_id_for,
    load_question_ids,
    normalize_question_ids,
    plan_batches,
)
from stackoverflow_vitality.answers.bronze import LocalAnswerBronzeStore
from stackoverflow_vitality.answers.checkpoint import CheckpointConflict, LocalAnswerCheckpoint
from stackoverflow_vitality.answers.collect import collect_answers
from stackoverflow_vitality.answers.config import AnswerCollectionConfig, load_answer_config
from stackoverflow_vitality.answers.history import content_hash, empty_state, observe, rebuild
from stackoverflow_vitality.answers.models import (
    SOURCE,
    STRATEGY,
    AnswerBronzePage,
    AnswerCheckpoint,
    payload_checksum,
)
from stackoverflow_vitality.answers.transform import AnswerTransformFailure, transform_answers
from stackoverflow_vitality.cli import main
from stackoverflow_vitality.ingestion.interfaces import ApiResponse
from stackoverflow_vitality.storage.files import (
    ImmutableConflict,
    LocalLockConflict,
    exclusive_lock,
)

ENDPOINT = "https://api.stackexchange.com/2.3/questions"
NOW = datetime(2026, 9, 21, tzinfo=UTC)
COLLECTED = datetime(2026, 9, 10, tzinfo=UTC)
CREATED = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
BATCH = "ab" * 32


class FakeHttp:
    def __init__(
        self, pages: dict[str, list[dict[str, object]]], *, fail: set[str] | None = None
    ) -> None:
        self.pages = pages
        self.fail = fail or set()
        self.calls = 0

    def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse:
        self.calls += 1
        if endpoint in self.fail:
            raise RuntimeError("batch failed")
        payload = self.pages[endpoint][int(params["page"]) - 1]
        items = payload["items"]
        assert isinstance(items, list)
        quota = payload.get("quota_remaining", 4)
        return ApiResponse(
            items=items,
            has_more=bool(payload["has_more"]),
            quota_remaining=quota if type(quota) is int else None,
            payload=payload,
        )


def test_batch_rules_reject_bad_ids_and_keep_hashes_stable() -> None:
    assert normalize_question_ids([3, 1, 2]) == (1, 2, 3)
    assert load_question_ids({"question_ids": [2, 1]}) == (1, 2)
    for raw in ([], [0], [-3], ["1"], [True], [1, 1], {"ids": [1]}):
        with pytest.raises(ValueError):
            load_question_ids(raw)
    with pytest.raises(ValueError, match="batch_index"):
        AnswerBatch(-1, BATCH, (1,), "https://api.stackexchange.com/2.3/questions/1/answers")
    with pytest.raises(ValueError, match="HTTPS"):
        answers_url("http://api.stackexchange.com/2.3/questions", (1,))
    with pytest.raises(ValueError, match="/questions"):
        answers_url("https://api.stackexchange.com/2.3/answers", (1,))
    with pytest.raises(ValueError, match="max_batch_size"):
        plan_batches(
            (1,), endpoint=ENDPOINT, max_batch_size=0, max_url_length=2000, query_allowance=0
        )
    with pytest.raises(ValueError, match="max_batch_size"):
        plan_batches(
            (1,), endpoint=ENDPOINT, max_batch_size=True, max_url_length=2000, query_allowance=0
        )  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_url_length"):
        plan_batches(
            (1,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=31, query_allowance=0
        )
    with pytest.raises(ValueError, match="query_allowance"):
        plan_batches(
            (1,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=2000, query_allowance=-1
        )
    with pytest.raises(ValueError, match="unique"):
        plan_batches(
            (1, 1), endpoint=ENDPOINT, max_batch_size=2, max_url_length=2000, query_allowance=0
        )
    with pytest.raises(ValueError, match="question ids"):
        plan_batches(
            (), endpoint=ENDPOINT, max_batch_size=1, max_url_length=2000, query_allowance=0
        )
    one = answers_url(ENDPOINT, (10,))
    assert plan_batches(
        (10,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=len(one) + 1, query_allowance=0
    )
    with pytest.raises(ValueError, match="single batch"):
        plan_batches(
            (10,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=len(one), query_allowance=0
        )
    assert batch_id_for((1, 2)) == batch_id_for((1, 2))
    assert batch_id_for((1, 2)) != batch_id_for((1, 3))


def test_config_precedence_and_rejections(tmp_path: Path) -> None:
    path = tmp_path / "answers.yml"
    path.write_text(
        "site: from-yaml\n"
        "endpoint: https://api.stackexchange.com/2.3/questions\n"
        "mode: incremental\n",
        encoding="utf-8",
    )
    assert load_answer_config(path, {}, {}).site == "from-yaml"
    assert load_answer_config(path, {}, {}).page_size == 100
    assert (
        load_answer_config(path, {"STACKEXCHANGE_ANSWERS_SITE": "from-env"}, {}).site == "from-env"
    )
    config = load_answer_config(
        path,
        {"STACKEXCHANGE_ANSWERS_SITE": "from-env", "STACKEXCHANGE_API_KEY": "secret-token"},
        {"site": "from-cli"},
    )
    assert config.site == "from-cli"
    assert "secret-token" not in json.dumps(config.__dict__)
    path.write_text("api_key: secret-token\n", encoding="utf-8")
    with pytest.raises(ValueError, match="secret"):
        load_answer_config(path, {}, {})
    path.write_text("not_a_field: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        load_answer_config(path, {}, {})
    path.write_text(
        "site: from-yaml\n"
        "endpoint: https://api.stackexchange.com/2.3/questions\n"
        "mode: incremental\n",
        encoding="utf-8",
    )
    missing = tmp_path / "missing.yml"
    with pytest.raises(ValueError, match="unreadable"):
        load_answer_config(missing, {}, {})
    path.write_text(":\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        load_answer_config(path, {}, {})
    path.write_text("- just-a-list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_answer_config(path, {}, {})
    path.write_text("page_size: no\n", encoding="utf-8")
    with pytest.raises(ValueError, match="page_size"):
        load_answer_config(path, {}, {})
    path.write_text("mode: sideways\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mode"):
        load_answer_config(path, {}, {})
    path.write_text("retry:\n  nope: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="retry"):
        load_answer_config(path, {}, {})
    path.write_text("max_attempts: 1\nretry:\n  max_attempts: 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nested"):
        load_answer_config(path, {}, {})
    path.write_text("site: stackoverflow\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown CLI"):
        load_answer_config(path, {}, {"token": "x"})
    env_endpoint = {"STACKEXCHANGE_ANSWERS_ENDPOINT": "http://insecure.example/questions"}
    with pytest.raises(ValueError, match="endpoint"):
        load_answer_config(path, env_endpoint, {})
    file_output = tmp_path / "not-a-dir"
    file_output.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        AnswerCollectionConfig(bronze_output=str(file_output)).validate()
    with pytest.raises(ValueError, match="directory"):
        AnswerCollectionConfig(bronze_output="bad\x00dir").validate()
    for broken in (
        AnswerCollectionConfig(endpoint="http://api.stackexchange.com/2.3/questions"),
        AnswerCollectionConfig(page_size=0),
        AnswerCollectionConfig(page_size=101),
        AnswerCollectionConfig(max_pages=0),
        AnswerCollectionConfig(max_batch_size=-1),
        AnswerCollectionConfig(max_url_length=10),
        AnswerCollectionConfig(query_allowance=-1),
        AnswerCollectionConfig(connect_timeout=0),
        AnswerCollectionConfig(read_timeout=-1),
        AnswerCollectionConfig(max_attempts=0),
        AnswerCollectionConfig(jitter=2),
        AnswerCollectionConfig(initial_backoff_seconds=80, max_backoff_seconds=1),
        AnswerCollectionConfig(concurrency=2),
        AnswerCollectionConfig(user_agent="bad\nagent"),
        AnswerCollectionConfig(site=""),
    ):
        with pytest.raises(ValueError):
            broken.validate()


def test_bronze_rejects_bad_identity_and_keeps_distinct_runs(tmp_path: Path) -> None:
    store = LocalAnswerBronzeStore(tmp_path)
    first = _page(uuid4())
    store.write_page(first)
    store.write_page(first)
    assert store.confirm_page(first)
    changed = _page(first.run_id, body="other")
    with pytest.raises(ImmutableConflict):
        store.write_page(changed)
    second = _page(uuid4(), body="later")
    store.write_page(second)
    assert store.confirm_page(first) and store.confirm_page(second)
    assert len(list(tmp_path.rglob("page=0001.json"))) == 2
    bad_checksum = _page(uuid4())
    object_checksum = "0" * 64
    forged = AnswerBronzePage(**{**bad_checksum.__dict__, "checksum": object_checksum})
    with pytest.raises(Exception, match="checksum"):
        store.write_page(forged)
    assert store.confirm_page(_page(uuid4())) is False
    assert store.read_manifest(uuid4()) is None
    store.write_manifest(
        first.run_id, {"schema_version": "answers_collection_manifest.v1", "status": "completed"}
    )
    assert store.read_manifest(first.run_id) is not None
    manifest = tmp_path / "answers" / "manifests" / f"{second.run_id}.json"
    manifest.write_text("[]", encoding="utf-8")
    assert store.read_manifest(second.run_id) is None
    broken = (
        tmp_path
        / "answers"
        / "site=stackoverflow"
        / f"run={first.run_id}"
        / f"b={BATCH[:16]}"
        / "page=0001.json"
    )
    broken.write_text("{", encoding="utf-8")
    assert store.confirm_page(first) is False
    with pytest.raises(ValueError, match="hex"):
        store.write_page(_page(uuid4(), batch_id="zz" * 32))
    with pytest.raises(ValueError):
        store.write_page(_page(uuid4(), site="../" * 8 + "outside"))


def test_checkpoint_cas_corruption_and_lock(tmp_path: Path) -> None:
    store = LocalAnswerCheckpoint(tmp_path)
    moment = datetime(2026, 9, 10, tzinfo=UTC)
    assert store.read(f"{SOURCE}|stackoverflow|{ENDPOINT}|{STRATEGY}") is None
    first = _cursor(("aa",), (1,), moment)
    store.update(None, first)
    assert store.read(first.key) == first
    grown = _cursor(("aa", "bb"), (1, 2), moment)
    store.update(first, grown)
    store.update(grown, grown)
    assert store.read(grown.key) == grown
    with pytest.raises(CheckpointConflict):
        store.update(first, grown)
    with pytest.raises(CheckpointConflict):
        store.update(grown, first)
    path = tmp_path / "answers" / "checkpoint.json"
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        store.read(first.key)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        store.read(first.key)
    path.write_text(json.dumps({"k": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        store.read("k")
    path.write_text(json.dumps({first.key: {"source": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        store.read(first.key)
    lock = tmp_path / "answers" / ".writer.lock"
    held = threading.Event()
    release = threading.Event()

    def occupy() -> None:
        with exclusive_lock(lock):
            held.set()
            assert release.wait(timeout=5)

    worker = threading.Thread(target=occupy)
    worker.start()
    assert held.wait(timeout=5)
    with pytest.raises(LocalLockConflict):
        with exclusive_lock(lock):
            pass
    release.set()
    worker.join(timeout=5)
    assert not lock.exists()
    try:
        with exclusive_lock(lock):
            raise RuntimeError("writer failed")
    except RuntimeError:
        pass
    assert not lock.exists()


def test_limited_run_does_not_advance_and_confirm_failure_releases_lock(tmp_path: Path) -> None:
    config = AnswerCollectionConfig(max_batch_size=1, bronze_output=str(tmp_path))
    batches = plan_batches(
        (10, 20), endpoint=ENDPOINT, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    http = FakeHttp(
        {
            batches[0].url: [{"items": [_item(1, 10)], "has_more": False}],
            batches[1].url: [{"items": [_item(2, 20)], "has_more": False}],
        }
    )
    limited = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        mode="incremental",
        max_batches=1,
        now=lambda: COLLECTED,
    )
    assert limited.status == "truncated"
    assert limited.checkpoint_advanced is False
    assert not (tmp_path / "answers" / "checkpoint.json").exists()
    assert not (tmp_path / "answers" / ".writer.lock").exists()
    with pytest.raises(ValueError, match="mode"):
        collect_answers(
            config,
            batches,
            origin="ids.json",
            run_id=uuid4(),
            http=http,
            bronze=LocalAnswerBronzeStore(tmp_path / "m"),
            checkpoints=LocalAnswerCheckpoint(tmp_path / "m"),
            mode="sideways",
            now=lambda: COLLECTED,
        )
    with pytest.raises(ValueError, match="question ids"):
        collect_answers(
            config,
            (),
            origin="ids.json",
            run_id=uuid4(),
            http=http,
            bronze=LocalAnswerBronzeStore(tmp_path),
            checkpoints=LocalAnswerCheckpoint(tmp_path),
            mode="incremental",
            now=lambda: COLLECTED,
        )


def test_quality_codes_drive_publication_status(tmp_path: Path) -> None:
    bronze, silver, quarantine = _roots(tmp_path)
    good = _item(1, 10, body="<p>Kept</p>", activity=CREATED)
    cases = [
        ({"question_id": 10, "body": "<p>x</p>", "creation_date": CREATED}, "SA-001", "error"),
        (_item(0, 10), "SA-001", "error"),
        ({"answer_id": 3, "body": "<p>x</p>", "creation_date": CREATED}, "SA-002", "error"),
        (_item(4, -1), "SA-002", "error"),
        (_item(5, 99), "SA-003", "error"),
        (_item(6, 10, created="nope"), "SA-004", "error"),
        (
            _item(7, 10, created=int(datetime(2026, 9, 10, 12, tzinfo=UTC).timestamp())),
            "SA-005",
            "error",
        ),
        (_item(8, 10, body=""), "SA-007", "error"),
        (_item(9, 10, score=True), "SA-006", "error"),
        (_item(12, 10, score=1.5), "SA-006", "error"),
        (_item(13, 10, accepted=1), "SA-006", "error"),
        (_item(14, 10, body="x" * 20, license=1), "SA-004", "error"),
        (_item(15, 10, owner_id=True), "SA-010", "error"),
        (_item(16, 10, body="y" * 500_001), "SA-007", "error"),
        ({**_item(18, 10), "answer_id": True}, "SA-001", "error"),
        ({**_item(19, 10), "question_id": True}, "SA-002", "error"),
        ({**_item(20, 10), "last_edit_date": CREATED - 10}, "SA-004", "error"),
        ({**_item(21, 10), "last_activity_date": 10**20}, "SA-004", "error"),
        (_item(11, 10, owner=None), "SA-010", "warning"),
        ("not-an-object", "SA-001", "error"),
    ]
    items = [good, *[item for item, _rule, _severity in cases]]
    LocalAnswerBronzeStore(bronze).write_page(_page(uuid4(), items=items))
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
        + "\n",
        encoding="utf-8",
    )
    result = transform_answers(
        bronze,
        silver,
        quarantine,
        questions_silver=questions,
        transformation_run_id="quality-run",
        now=lambda: NOW,
    )
    assert result.status == "partial"
    assert result.reconciliation == "compared"
    rules = {
        (row["rule_id"], row["severity"])
        for row in _jsonl(
            quarantine / "answers" / "transformation_run_id=quality-run" / "items.jsonl"
        )
    }
    for _item_payload, rule, severity in cases:
        assert (rule, severity) in rules
    reconciliation = {
        row["rule_id"]
        for row in _jsonl(
            quarantine / "answers" / "transformation_run_id=quality-run" / "reconciliation.jsonl"
        )
    }
    assert "SA-013" in reconciliation
    manifest = json.loads(
        (silver / "answers" / "manifests" / "quality-run.json").read_text(encoding="utf-8")
    )
    assert manifest["coverage_complete"] is False
    versions = _jsonl(next(silver.rglob("content_versions.jsonl")))
    observations = _jsonl(next(silver.rglob("observations.jsonl")))
    assert all("last_activity_at" not in row for row in versions)
    assert any(row["last_activity_at"] == "2026-09-01T00:00:00Z" for row in observations)


def test_transform_history_replay_and_failure_do_not_replace_the_manifest(tmp_path: Path) -> None:
    bronze, silver, quarantine = _roots(tmp_path)
    early = _page(
        uuid4(),
        items=[_item(1, 10, body="<p>A</p>", score=1, accepted=False)],
        recovered=datetime(2026, 9, 12, tzinfo=UTC),
        page=1,
    )
    late = _page(
        early.run_id,
        items=[_item(1, 10, body="<p>A</p>", score=4, accepted=True, activity=CREATED + 10)],
        recovered=datetime(2026, 9, 11, tzinfo=UTC),
        page=2,
        batch_index=1,
        batch_id="cd" * 32,
    )
    store = LocalAnswerBronzeStore(bronze)
    store.write_page(early)
    store.write_page(late)
    first = transform_answers(
        bronze, silver, quarantine, transformation_run_id="hist", now=lambda: NOW
    )
    assert first.status == "completed"
    versions = _jsonl(next(silver.rglob("content_versions.jsonl")))
    observations = _jsonl(next(silver.rglob("observations.jsonl")))
    assert len(versions) == 1
    assert versions[0]["valid_from"] == "2026-09-11T00:00:00Z"
    assert "last_activity_at" not in versions[0]
    assert [row["score"] for row in sorted(observations, key=lambda row: row["collected_at"])] == [
        4,
        1,
    ]
    assert [
        row["is_accepted"] for row in sorted(observations, key=lambda row: row["collected_at"])
    ] == [True, False]
    replay = transform_answers(
        bronze, silver, quarantine, transformation_run_id="hist", now=lambda: NOW
    )
    assert replay.observations == first.observations
    manifest_before = (silver / "answers" / "manifests" / "hist.json").read_bytes()
    store.write_page(_page(uuid4(), items=[_item(2, 10, body="<p>B</p>")], batch_id="ef" * 32))
    with pytest.raises(ImmutableConflict):
        transform_answers(bronze, silver, quarantine, transformation_run_id="hist", now=lambda: NOW)
    assert (silver / "answers" / "manifests" / "hist.json").read_bytes() == manifest_before
    empty_bronze, empty_silver, empty_quarantine = _roots(tmp_path / "empty")
    empty = transform_answers(
        empty_bronze, empty_silver, empty_quarantine, transformation_run_id="empty", now=lambda: NOW
    )
    assert empty.status == "empty"
    assert empty.pages_read == 0
    with pytest.raises(ValueError, match="positive"):
        transform_answers(bronze, silver, quarantine, max_body_chars=0)
    with pytest.raises(ValueError, match="overlap"):
        transform_answers(bronze, bronze, quarantine)
    with pytest.raises(AnswerTransformFailure, match="missing"):
        transform_answers(bronze, silver, quarantine, questions_silver=tmp_path / "nope.jsonl")


def test_same_instant_tie_is_a_warning_and_conflict_is_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    left = _record(body="<p>left</p>", collected="2026-09-11T00:00:00Z", index=0)
    right = _record(body="<p>right</p>", collected="2026-09-11T00:00:00Z", index=1)
    state = empty_state()
    observe(state, left)
    assert observe(state, left) is False
    observe(state, right)
    ties = rebuild(state)
    assert ties
    assert len(state["versions"]) == 1
    assert content_hash(left) != content_hash(right)
    conflict = dict(left)
    conflict["body_text"] = "tampered"
    conflict["source_item_index"] = 9
    fresh = empty_state()
    observe(fresh, left)
    with pytest.raises(ValueError, match="catalog"):
        observe(fresh, conflict)
    bronze, silver, quarantine = _roots(tmp_path)
    LocalAnswerBronzeStore(bronze).write_page(_page(uuid4(), items=[_item(1, 10), _item(2, 10)]))
    real = observe

    def explode(history: dict[str, object], record: dict[str, object]) -> bool:
        if record["answer_id"] == 2:
            raise ValueError("observation identity conflict")
        return real(history, record)

    monkeypatch.setattr("stackoverflow_vitality.answers.transform.observe", explode)
    result = transform_answers(
        bronze, silver, quarantine, transformation_run_id="conflict", now=lambda: NOW
    )
    assert result.status == "partial"
    assert result.quarantined_records == 1
    rows = _jsonl(quarantine / "answers" / "transformation_run_id=conflict" / "items.jsonl")
    assert rows[0]["rule_id"] == "SA-009"
    assert rows[0]["severity"] == "error"


def test_cli_dry_run_writes_nothing_and_simulated_collect_stays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = tmp_path / "ids.json"
    ids.write_text("[10, 20]", encoding="utf-8")
    bronze = tmp_path / "bronze"
    config = tmp_path / "answers.yml"
    config.write_text(
        "site: stackoverflow\n"
        "endpoint: https://api.stackexchange.com/2.3/questions\n"
        f"bronze_output: {bronze.as_posix()}\n"
        "mode: incremental\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKEXCHANGE_API_KEY", "secret-token")

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run constructed a client")

    monkeypatch.setattr("stackoverflow_vitality.answers.cli.HttpxStackExchangeClient", explode)
    assert (
        main(
            [
                "collect-answers",
                "--config",
                str(config),
                "--question-ids-file",
                str(ids),
                "--dry-run",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert json.loads(output)["mode"] == "incremental"
    assert "secret-token" not in output
    assert not bronze.exists()

    class Stub:
        def __init__(self, **_kwargs: object) -> None:
            self.closed = False

        def get(self, _endpoint: str, _params: dict[str, str | int]) -> ApiResponse:
            payload = {"items": [], "has_more": False, "quota_remaining": 3}
            return ApiResponse(items=[], has_more=False, quota_remaining=3, payload=payload)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("stackoverflow_vitality.answers.cli.HttpxStackExchangeClient", Stub)
    assert main(["collect-answers", "--config", str(config), "--question-ids-file", str(ids)]) == 0
    ran = json.loads(capsys.readouterr().out)
    assert ran["status"] == "empty"
    assert ran["checkpoint_advanced"] is True
    assert "secret-token" not in json.dumps(ran)
    silver, quarantine = tmp_path / "silver", tmp_path / "quarantine"
    silver.mkdir()
    quarantine.mkdir()
    code = main(
        [
            "transform-answers",
            "--bronze-path",
            str(bronze),
            "--silver-path",
            str(silver),
            "--quarantine-path",
            str(quarantine),
            "--dry-run",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    assert list(silver.rglob("*.jsonl")) == []
    with pytest.raises(SystemExit) as failure:
        main(
            [
                "transform-answers",
                "--bronze-path",
                str(tmp_path / "missing"),
                "--silver-path",
                str(silver),
                "--quarantine-path",
                str(quarantine),
            ]
        )
    assert failure.value.code == 2

    failed_root = tmp_path / "failed"
    failed_bronze = failed_root / "bronze"
    failed_config = failed_root / "answers.yml"
    failed_root.mkdir()
    failed_config.write_text(
        "site: stackoverflow\n"
        "endpoint: https://api.stackexchange.com/2.3/questions\n"
        f"bronze_output: {failed_bronze.as_posix()}\n"
        "mode: incremental\n",
        encoding="utf-8",
    )

    class Boom:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def get(self, _endpoint: str, _params: dict[str, str | int]) -> ApiResponse:
            raise RuntimeError("offline failure")

        def close(self) -> None:
            return None

    monkeypatch.setattr("stackoverflow_vitality.answers.cli.HttpxStackExchangeClient", Boom)
    assert (
        main(["collect-answers", "--config", str(failed_config), "--question-ids-file", str(ids)])
        == 1
    )
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "failed"
    assert failed["checkpoint_advanced"] is False
    assert "secret-token" not in json.dumps(failed)
    assert not (failed_bronze / "answers" / "checkpoint.json").exists()
    assert not (failed_bronze / "answers" / ".writer.lock").exists()


def test_page_failures_and_acceptance_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bronze, silver, quarantine = _roots(tmp_path)
    store = LocalAnswerBronzeStore(bronze)
    run = uuid4()
    store.write_page(_page(run, items=[_item(1, 10, accepted=True), _item(2, 10, accepted=True)]))
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        "\n".join(
            [
                "",
                json.dumps({"site": "stackoverflow", "question_id": "bad", "is_current": True}),
                json.dumps(
                    {
                        "site": "stackoverflow",
                        "question_id": 10,
                        "is_current": False,
                        "accepted_answer_id": 1,
                    }
                ),
                json.dumps(
                    {
                        "site": "stackoverflow",
                        "question_id": 10,
                        "is_current": True,
                        "accepted_answer_id": 1,
                    }
                ),
                json.dumps(
                    {
                        "site": "stackoverflow",
                        "question_id": 11,
                        "is_current": True,
                        "accepted_answer_id": 50,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    compared = transform_answers(
        bronze,
        silver,
        quarantine,
        questions_silver=questions,
        transformation_run_id="acceptance",
        now=lambda: NOW,
    )
    assert compared.status == "partial"
    rules = {
        row["rule_id"]
        for row in _jsonl(
            quarantine / "answers" / "transformation_run_id=acceptance" / "reconciliation.jsonl"
        )
    }
    assert rules == {"SA-011", "SA-012", "SA-013"}
    versions = _jsonl(next((silver / "answers").rglob("content_versions.jsonl")))
    observations = _jsonl(next((silver / "answers").rglob("observations.jsonl")))
    assert observations
    assert all("last_activity_at" not in row for row in versions)
    assert all("last_activity_at" in row for row in observations)

    drifted = next(answers_pages(bronze))
    payload = json.loads(drifted.read_text(encoding="utf-8"))
    payload["schema_version"] = "bronze_stackexchange_answers_snapshot.v0"
    drifted.write_text(json.dumps(payload), encoding="utf-8")
    drift_silver = tmp_path / "drift-silver"
    drift_quarantine = tmp_path / "drift-quarantine"
    drift_silver.mkdir()
    drift_quarantine.mkdir()
    with pytest.raises(AnswerTransformFailure, match="schema drift"):
        transform_answers(bronze, drift_silver, drift_quarantine, transformation_run_id="drift")
    assert not (drift_silver / "answers" / "manifests" / "drift.json").exists()

    empty_items = tmp_path / "blank"
    blank_bronze, blank_silver, blank_quarantine = _roots(empty_items)
    LocalAnswerBronzeStore(blank_bronze).write_page(_page(uuid4(), items=[]))
    blank = transform_answers(
        blank_bronze, blank_silver, blank_quarantine, transformation_run_id="blank", now=lambda: NOW
    )
    assert blank.status == "empty"
    assert blank.answers_published == 0

    broken = next(answers_pages(blank_bronze))
    document = json.loads(broken.read_text(encoding="utf-8"))
    document["payload"] = {"items": {"not": "a list"}}
    document["checksum"] = payload_checksum(document["payload"])
    broken.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AnswerTransformFailure, match="items array"):
        transform_answers(
            blank_bronze, blank_silver, blank_quarantine, transformation_run_id="nolist"
        )

    unreadable = next(answers_pages(blank_bronze))
    unreadable.write_text("{", encoding="utf-8")
    with pytest.raises(AnswerTransformFailure, match="unreadable"):
        transform_answers(
            blank_bronze, blank_silver, blank_quarantine, transformation_run_id="broken"
        )

    other = tmp_path / "filter"
    filter_bronze, filter_silver, filter_quarantine = _roots(other)
    LocalAnswerBronzeStore(filter_bronze).write_page(_page(run, items=[_item(1, 10)]))
    skipped = transform_answers(
        filter_bronze,
        filter_silver,
        filter_quarantine,
        site="ja.stackoverflow",
        transformation_run_id="filtered-site",
        now=lambda: NOW,
    )
    assert skipped.pages_read == 0
    run_silver = tmp_path / "filter-run-silver"
    run_quarantine = tmp_path / "filter-run-quarantine"
    run_silver.mkdir()
    run_quarantine.mkdir()
    other_run = transform_answers(
        filter_bronze,
        run_silver,
        run_quarantine,
        run_id=str(uuid4()),
        transformation_run_id="filtered-run",
        now=lambda: NOW,
    )
    assert other_run.status == "empty"
    assert other_run.pages_read == 0

    checksum_bronze, checksum_silver, checksum_quarantine = _roots(tmp_path / "checksum")
    LocalAnswerBronzeStore(checksum_bronze).write_page(_page(uuid4(), items=[_item(1, 10)]))
    tampered = next(answers_pages(checksum_bronze))
    document = json.loads(tampered.read_text(encoding="utf-8"))
    document["payload"]["items"][0]["body"] = "<p>tampered</p>"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AnswerTransformFailure, match="checksum"):
        transform_answers(
            checksum_bronze,
            checksum_silver,
            checksum_quarantine,
            transformation_run_id="tampered",
            now=lambda: NOW,
        )
    assert not (checksum_silver / "answers" / "manifests" / "tampered.json").exists()

    def deny(self: LocalAnswerBronzeStore, page: AnswerBronzePage) -> bool:
        del self, page
        return False

    monkeypatch.setattr(LocalAnswerBronzeStore, "confirm_page", deny)
    config = AnswerCollectionConfig(bronze_output=str(tmp_path / "confirm"))
    batches = plan_batches(
        (10,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=2000, query_allowance=20
    )
    http = FakeHttp({batches[0].url: [{"items": [_item(1, 10)], "has_more": False}]})
    denied = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=http,
        bronze=LocalAnswerBronzeStore(tmp_path / "confirm"),
        checkpoints=LocalAnswerCheckpoint(tmp_path / "confirm"),
        now=lambda: COLLECTED,
    )
    assert denied.status == "failed"
    assert denied.checkpoint_advanced is False
    assert not (tmp_path / "confirm" / "answers" / ".writer.lock").exists()
    with pytest.raises(ValueError, match="max_pages"):
        collect_answers(
            config,
            batches,
            origin="ids.json",
            run_id=uuid4(),
            http=http,
            bronze=LocalAnswerBronzeStore(tmp_path / "limits"),
            checkpoints=LocalAnswerCheckpoint(tmp_path / "limits"),
            max_pages=0,
            now=lambda: COLLECTED,
        )
    with pytest.raises(ValueError, match="max_batches"):
        collect_answers(
            config,
            batches,
            origin="ids.json",
            run_id=uuid4(),
            http=http,
            bronze=LocalAnswerBronzeStore(tmp_path / "limits"),
            checkpoints=LocalAnswerCheckpoint(tmp_path / "limits"),
            max_batches=0,
            now=lambda: COLLECTED,
        )


def test_content_changes_open_versions_while_activity_stays_observational(tmp_path: Path) -> None:
    bronze, silver, quarantine = _roots(tmp_path)
    run = uuid4()
    store = LocalAnswerBronzeStore(bronze)
    edited = _item(1, 10, body="<p>B</p>", activity=CREATED + 10)
    edited["last_edit_date"] = CREATED + 100
    owned = _item(1, 10, body="<p>B</p>", owner=8, activity=CREATED + 20)
    owned["last_edit_date"] = CREATED + 100
    licensed = _item(1, 10, body="<p>B</p>", owner=8, license="CC BY-SA 3.0", activity=CREATED + 30)
    licensed["last_edit_date"] = CREATED + 100
    snapshots = (
        (
            _item(1, 10, body="<p>A</p>", score=1, activity=CREATED),
            datetime(2026, 9, 12, tzinfo=UTC),
            "ab" * 32,
        ),
        (edited, datetime(2026, 9, 10, tzinfo=UTC), "cd" * 32),
        (owned, datetime(2026, 9, 14, tzinfo=UTC), "ef" * 32),
        (licensed, datetime(2026, 9, 13, tzinfo=UTC), "12" * 32),
    )
    for index, (item, moment, batch) in enumerate(snapshots):
        store.write_page(
            _page(run, items=[item], recovered=moment, page=1, batch_id=batch, batch_index=index)
        )
    result = transform_answers(
        bronze, silver, quarantine, transformation_run_id="content", now=lambda: NOW
    )
    assert result.status == "completed"
    versions = _jsonl(next((silver / "answers").rglob("content_versions.jsonl")))
    observations = _jsonl(next((silver / "answers").rglob("observations.jsonl")))
    assert [row["valid_from"] for row in versions] == [
        "2026-09-10T00:00:00Z",
        "2026-09-12T00:00:00Z",
        "2026-09-13T00:00:00Z",
        "2026-09-14T00:00:00Z",
    ]
    assert [row["body_html"] for row in versions] == [
        "<p>B</p>",
        "<p>A</p>",
        "<p>B</p>",
        "<p>B</p>",
    ]
    assert versions[-1]["owner_user_id"] == 8
    assert versions[-1]["content_license"] == "CC BY-SA 4.0"
    assert versions[-2]["content_license"] == "CC BY-SA 3.0"
    assert all("last_activity_at" not in row for row in versions)
    assert all("last_activity_at" in row for row in observations)
    zone_bronze, zone_silver, zone_quarantine = _roots(tmp_path / "zone")
    LocalAnswerBronzeStore(zone_bronze).write_page(_page(uuid4(), items=[_item(1, 10)]))
    naive = next(answers_pages(zone_bronze))
    document = json.loads(naive.read_text(encoding="utf-8"))
    document["recovered_at"] = "2026-09-12T00:00:00"
    naive.write_text(json.dumps(document), encoding="utf-8")
    zoned = transform_answers(
        zone_bronze, zone_silver, zone_quarantine, transformation_run_id="zone", now=lambda: NOW
    )
    assert zoned.status == "partial"
    assert zoned.answers_published == 0
    zone_rules = {
        (row["rule_id"], row["severity"])
        for row in _jsonl(
            zone_quarantine / "answers" / "transformation_run_id=zone" / "items.jsonl"
        )
    }
    assert ("SA-004", "error") in zone_rules


def test_manifest_write_failure_releases_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("manifest volume is full")

    monkeypatch.setattr(LocalAnswerBronzeStore, "write_manifest", explode)
    config = AnswerCollectionConfig(bronze_output=str(tmp_path))
    batches = plan_batches(
        (10,), endpoint=ENDPOINT, max_batch_size=1, max_url_length=2000, query_allowance=20
    )

    class Down:
        def get(self, _endpoint: str, _params: dict[str, str | int]) -> ApiResponse:
            raise RuntimeError("down")

    failed = collect_answers(
        config,
        batches,
        origin="ids.json",
        run_id=uuid4(),
        http=Down(),
        bronze=LocalAnswerBronzeStore(tmp_path),
        checkpoints=LocalAnswerCheckpoint(tmp_path),
        now=lambda: COLLECTED,
    )
    assert failed.status == "failed"
    assert failed.checkpoint_advanced is False
    assert not (tmp_path / "answers" / "checkpoint.json").exists()
    assert not (tmp_path / "answers" / ".writer.lock").exists()


def test_acceptance_disagreement_and_same_instant_warning(tmp_path: Path) -> None:
    bronze, silver, quarantine = _roots(tmp_path)
    LocalAnswerBronzeStore(bronze).write_page(
        _page(
            uuid4(),
            items=[
                _item(1, 10, body="<p>One</p>", accepted=False),
                _item(1, 10, body="<p>Two</p>"),
            ],
        )
    )
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        json.dumps(
            {
                "site": "stackoverflow",
                "question_id": 10,
                "is_current": True,
                "accepted_answer_id": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = transform_answers(
        bronze,
        silver,
        quarantine,
        questions_silver=questions,
        transformation_run_id="disagree",
        now=lambda: NOW,
    )
    assert result.status == "partial"
    assert result.answers_published == 1
    quality = quarantine / "answers" / "transformation_run_id=disagree"
    rules = {(row["rule_id"], row["severity"]) for row in _jsonl(quality / "items.jsonl")}
    reconciliation = {row["rule_id"] for row in _jsonl(quality / "reconciliation.jsonl")}
    assert ("SA-016", "warning") in rules
    assert "SA-011" in reconciliation
    manifest = json.loads(
        (silver / "answers" / "manifests" / "disagree.json").read_text(encoding="utf-8")
    )
    assert manifest["coverage_complete"] is False
    versions = _jsonl(next((silver / "answers").rglob("content_versions.jsonl")))
    assert len(versions) == 1
    assert "last_activity_at" not in versions[0]


def test_envelope_defects_reject_the_page_item(tmp_path: Path) -> None:
    bronze, silver, quarantine = _roots(tmp_path)
    store = LocalAnswerBronzeStore(bronze)
    batches = ("ab" * 32, "cd" * 32, "ef" * 32, "12" * 32, "34" * 32)
    for batch in batches:
        created = 1_000_000_000 if batch.startswith("34") else CREATED
        store.write_page(_page(uuid4(), items=[_item(1, 10, created=created)], batch_id=batch))
    pages = sorted(answers_pages(bronze))
    untouched = "b=" + ("34" * 8)
    others = [page for page in pages if untouched not in page.as_posix()]
    mutations = (
        ("recovered_at", "2026-99-99T00:00:00Z"),
        ("run_id", "not-a-uuid"),
        ("page", 0),
        ("site", " "),
    )
    for page, (field, value) in zip(others, mutations, strict=True):
        document = json.loads(page.read_text(encoding="utf-8"))
        document[field] = value
        page.write_text(json.dumps(document), encoding="utf-8")
    result = transform_answers(
        bronze, silver, quarantine, transformation_run_id="envelope", now=lambda: NOW
    )
    assert result.status == "partial"
    assert result.answers_published == 0
    rules = {
        (row["rule_id"], row["severity"])
        for row in _jsonl(quarantine / "answers" / "transformation_run_id=envelope" / "items.jsonl")
    }
    assert rules == {("SA-004", "error")}
    with pytest.raises(ValueError, match="existing"):
        transform_answers(tmp_path / "missing-bronze", silver, quarantine)


def test_checkpoint_and_model_reject_corrupt_identity(tmp_path: Path) -> None:
    store = LocalAnswerCheckpoint(tmp_path)
    moment = datetime(2026, 9, 10, tzinfo=UTC)
    first = _cursor(("aa",), (1,), moment)
    grown = _cursor(("aa", "bb"), (1, 2), moment)
    store.update(None, first)
    with pytest.raises(CheckpointConflict):
        store.update(None, grown)
    path = tmp_path / "answers" / "checkpoint.json"
    path.unlink()
    with pytest.raises(CheckpointConflict):
        store.update(first, grown)
    store.update(None, grown)
    shrunk = _cursor(("aa", "bb"), (1,), moment)
    with pytest.raises(CheckpointConflict):
        store.update(grown, shrunk)
    raw = json.loads(path.read_text(encoding="utf-8"))
    record = raw[grown.key]
    record["completed_question_ids"] = [0]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        store.read(grown.key)
    record["completed_question_ids"] = [1, 2]
    record["source"] = 1
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        store.read(grown.key)
    page = _page(uuid4())
    naive = datetime(2026, 9, 10)
    mutations = (
        {"schema_version": "bronze_stackexchange_answers_snapshot.v0"},
        {"strategy": "tags"},
        {"source": "dump"},
        {"page": 0},
        {"batch_index": -1},
        {"question_ids": ()},
        {"http_status": 404},
        {"requested_at": naive},
        {"request_params": {"key": "secret"}},
        {"checksum": "abc"},
    )
    for broken in mutations:
        with pytest.raises(ValueError):
            replace(page, **broken)
    with pytest.raises(ValueError, match="strategy"):
        replace(first, strategy="tags")
    with pytest.raises(ValueError, match="sorted"):
        replace(first, completed_batch_ids=("bb", "aa"))
    with pytest.raises(ValueError, match="sorted"):
        replace(first, completed_question_ids=(2, 1))


def test_cli_rejects_invalid_input_and_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = tmp_path / "ids.json"
    ids.write_text("{}", encoding="utf-8")
    config = tmp_path / "answers.yml"
    bronze = tmp_path / "bronze"
    config.write_text(
        "site: stackoverflow\n"
        "endpoint: https://api.stackexchange.com/2.3/questions\n"
        f"bronze_output: {bronze.as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "stackoverflow_vitality.answers.cli.HttpxStackExchangeClient",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("client constructed")),
    )
    with pytest.raises(SystemExit) as invalid:
        main(
            [
                "collect-answers",
                "--config",
                str(config),
                "--question-ids-file",
                str(ids),
                "--dry-run",
            ]
        )
    assert invalid.value.code == 2
    assert not bronze.exists()
    silver, quarantine = tmp_path / "silver", tmp_path / "quarantine"
    for path in (bronze, silver, quarantine):
        path.mkdir()
    with pytest.raises(SystemExit) as limit:
        main(
            [
                "transform-answers",
                "--bronze-path",
                str(bronze),
                "--silver-path",
                str(silver),
                "--quarantine-path",
                str(quarantine),
                "--max-body-chars",
                "0",
            ]
        )
    assert limit.value.code == 2
    LocalAnswerBronzeStore(bronze).write_page(_page(uuid4()))
    drifted = next(answers_pages(bronze))
    document = json.loads(drifted.read_text(encoding="utf-8"))
    document["schema_version"] = "bronze_stackexchange_answers_snapshot.v0"
    drifted.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SystemExit) as drift:
        main(
            [
                "transform-answers",
                "--bronze-path",
                str(bronze),
                "--silver-path",
                str(silver),
                "--quarantine-path",
                str(quarantine),
            ]
        )
    assert drift.value.code == 1
    assert "schema drift" in capsys.readouterr().err
    assert not (silver / "answers" / "manifests").exists()


def answers_pages(bronze: Path) -> Iterator[Path]:
    return (bronze / "answers").glob("site=*/run=*/b=*/page=*.json")


def _roots(root: Path) -> tuple[Path, Path, Path]:
    bronze, silver, quarantine = root / "bronze", root / "silver", root / "quarantine"
    for path in (bronze, silver, quarantine):
        path.mkdir(parents=True)
    return bronze, silver, quarantine


def _item(
    answer_id: int,
    question_id: int,
    *,
    body: str = "<p>Body</p>",
    score: object = 1,
    accepted: object = False,
    owner: int | None = 4,
    owner_id: object | None = None,
    created: object = CREATED,
    activity: int | None = None,
    license: object = "CC BY-SA 4.0",
) -> dict[str, object]:
    item: dict[str, object] = {
        "answer_id": answer_id,
        "question_id": question_id,
        "creation_date": created,
        "body": body,
        "score": score,
        "is_accepted": accepted,
        "content_license": license,
    }
    if owner_id is not None:
        item["owner"] = {"user_id": owner_id}
    elif owner is not None:
        item["owner"] = {"user_id": owner}
    if activity is not None:
        item["last_activity_date"] = activity
    return item


def _page(
    run_id: object,
    *,
    body: str = "<p>Body</p>",
    items: list[dict[str, object]] | None = None,
    recovered: datetime = COLLECTED,
    page: int = 1,
    batch_id: str = BATCH,
    batch_index: int = 0,
    site: str = "stackoverflow",
    questions: tuple[int, ...] = (10,),
) -> AnswerBronzePage:
    payload = {
        "items": items if items is not None else [_item(1, 10, body=body)],
        "has_more": False,
    }
    return AnswerBronzePage(
        run_id=run_id,  # type: ignore[arg-type]
        source=SOURCE,
        endpoint=f"{ENDPOINT}/10/answers",
        site=site,
        strategy=STRATEGY,
        batch_id=batch_id,
        batch_index=batch_index,
        question_ids=questions,
        question_ids_origin="ids.json",
        page=page,
        requested_at=recovered,
        recovered_at=recovered,
        payload=payload,
        request_params={
            "site": "stackoverflow",
            "page": page,
            "pagesize": 100,
            "filter": "withbody",
        },
        has_more=False,
        quota_max=None,
        quota_remaining=9,
        backoff_seconds=None,
        http_status=200,
        collector_version="0.3.0",
        user_agent="test",
        item_count=len(payload["items"]),
        truncated=False,
        checksum=payload_checksum(payload),
    )


def _cursor(
    batches: tuple[str, ...], questions: tuple[int, ...], moment: datetime
) -> AnswerCheckpoint:
    return AnswerCheckpoint(SOURCE, "stackoverflow", ENDPOINT, STRATEGY, batches, questions, moment)


def _record(*, body: str, collected: str, index: int) -> dict[str, object]:
    return {
        "schema_version": "silver_answer_content_versions.v1",
        "entity_type": "answer",
        "site": "stackoverflow",
        "answer_id": 1,
        "question_id": 10,
        "creation_at": "2026-09-01T00:00:00Z",
        "last_edit_at": None,
        "last_activity_at": None,
        "owner_user_id": 7,
        "body_html": body,
        "body_text": "text",
        "code_blocks": [],
        "content_license": "CC BY-SA 4.0",
        "collected_at": collected,
        "score": 1,
        "is_accepted": False,
        "source_run_id": "run",
        "source_page": 1,
        "source_batch_id": "batch",
        "source_file_checksum": "c" * 64,
        "source_item_index": index,
        "warnings": [],
    }


def _jsonl(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines()]
