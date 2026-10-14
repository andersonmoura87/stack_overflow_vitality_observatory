"""Collect answers by deterministic question-id batches. No answer_id watermark."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from stackoverflow_vitality.answers.batching import AnswerBatch
from stackoverflow_vitality.answers.bronze import LocalAnswerBronzeStore
from stackoverflow_vitality.answers.checkpoint import LocalAnswerCheckpoint
from stackoverflow_vitality.answers.config import AnswerCollectionConfig
from stackoverflow_vitality.answers.models import (
    COLLECTOR_VERSION,
    SOURCE,
    STRATEGY,
    AnswerBronzePage,
    AnswerCheckpoint,
    payload_checksum,
)
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.ingestion.interfaces import ApiResponse, HttpClient
from stackoverflow_vitality.storage.files import LocalLockConflict, exclusive_lock

LOGGER = logging.getLogger(__name__)
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


class AnswerCollectionError(RuntimeError):
    """A batch failed or the page budget was exhausted."""


@dataclass(frozen=True)
class AnswerCollectionResult:
    run_id: UUID
    status: str
    batches_requested: int
    batches_completed: int
    pages_persisted: int
    items_received: int
    duplicates: int
    outside_batch: int
    truncated: bool
    question_ids_requested: tuple[int, ...]
    question_ids_completed: tuple[int, ...]
    question_ids_pending: tuple[int, ...]
    checkpoint_advanced: bool
    errors: tuple[str, ...] = ()


def collect_answers(
    config: AnswerCollectionConfig,
    batches: tuple[AnswerBatch, ...],
    *,
    origin: str,
    run_id: UUID,
    http: HttpClient,
    bronze: LocalAnswerBronzeStore,
    checkpoints: LocalAnswerCheckpoint,
    mode: str = "incremental",
    max_batches: int | None = None,
    max_pages: int | None = None,
    now: Clock = lambda: datetime.now(UTC),
    sleeper: Sleeper = lambda _seconds: None,
) -> AnswerCollectionResult:
    """Write one immutable snapshot under this run_id.

    Incremental collection fetches only batches absent from the checkpoint
    and advances that cursor after a complete success. Refresh fetches every
    requested batch, including ones already checkpointed, and does not move
    the cursor. Failure and truncation publish a non-success manifest and
    also leave the checkpoint unchanged. On incremental success the
    checkpoint is updated and the manifest is published after it.
    """
    config.validate()
    if mode not in {"incremental", "refresh"}:
        raise ValueError("mode must be incremental or refresh")
    if not batches:
        raise ValueError("batch must contain question ids")
    page_limit = config.max_pages if max_pages is None else max_pages
    if type(page_limit) is not int or page_limit < 1:
        raise ValueError("max_pages must be positive")
    selected = batches if max_batches is None else batches[:max_batches]
    if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
        raise ValueError("max_batches must be positive")
    limited = max_batches is not None and len(selected) < len(batches)
    started = now()
    key = f"{SOURCE}|{config.site}|{config.endpoint}|{STRATEGY}"
    pages_persisted = 0
    items_received = 0
    duplicates = 0
    outside_batch = 0
    skipped = 0
    seen: set[int] = set()
    completed: list[AnswerBatch] = []
    truncated = False
    errors: list[str] = []
    quota_remaining: int | None = None

    def result(state: str, *, advanced: bool) -> AnswerCollectionResult:
        done_ids = tuple(
            sorted(question_id for batch in completed for question_id in batch.question_ids)
        )
        requested = tuple(
            sorted(question_id for batch in batches for question_id in batch.question_ids)
        )
        pending = tuple(
            question_id for question_id in requested if question_id not in set(done_ids)
        )
        return AnswerCollectionResult(
            run_id=run_id,
            status=state,
            batches_requested=len(batches),
            batches_completed=len(completed),
            pages_persisted=pages_persisted,
            items_received=items_received,
            duplicates=duplicates,
            outside_batch=outside_batch,
            truncated=truncated,
            question_ids_requested=requested,
            question_ids_completed=done_ids,
            question_ids_pending=pending,
            checkpoint_advanced=advanced,
            errors=tuple(errors),
        )

    try:
        with exclusive_lock(bronze.root / "answers" / ".writer.lock"):
            stored = checkpoints.read(key) if mode == "incremental" else None
            already = set() if stored is None else set(stored.completed_batch_ids)
            for batch in selected:
                if batch.batch_id in already:
                    completed.append(batch)
                    skipped += 1
                    continue
                page_number = 1
                while True:
                    requested_at = now()
                    params: dict[str, str | int] = {
                        "site": config.site,
                        "page": page_number,
                        "pagesize": config.page_size,
                        "filter": config.api_filter,
                    }
                    response = http.get(batch.url, params)
                    if response.backoff_seconds:
                        LOGGER.info(
                            json.dumps(
                                {
                                    "event": "answer_api_backoff",
                                    "run_id": str(run_id),
                                    "site": config.site,
                                    "batch_index": batch.batch_index,
                                    "backoff_seconds": response.backoff_seconds,
                                },
                                sort_keys=True,
                            )
                        )
                        sleeper(float(response.backoff_seconds))
                    quota_remaining = response.quota_remaining
                    payload = response.payload or {
                        "items": response.items,
                        "has_more": response.has_more,
                    }
                    received, duplicate_count, outside = _account(payload, batch, seen)
                    items_received += received
                    duplicates += duplicate_count
                    outside_batch += outside
                    page = _page(
                        config,
                        batch,
                        origin,
                        run_id,
                        page_number,
                        requested_at,
                        now(),
                        payload,
                        params,
                        response,
                    )
                    bronze.write_page(page)
                    if not bronze.confirm_page(page):
                        raise AnswerCollectionError("answer page checksum confirmation failed")
                    pages_persisted += 1
                    _log_page(run_id, config.site, batch, page_number, received, response)
                    if response.has_more and response.quota_remaining == 0:
                        raise AnswerCollectionError("quota exhausted with has_more")
                    if not response.has_more:
                        break
                    if page_number >= page_limit:
                        truncated = True
                        raise AnswerCollectionError("answer page limit reached with has_more")
                    page_number += 1
                completed.append(batch)
            if limited:
                truncated = True
            if truncated or len(completed) != len(batches):
                incomplete = result("truncated" if truncated else "failed", advanced=False)
                _write_manifest(bronze, config, incomplete, started, now(), mode, quota_remaining)
                return incomplete
            status = "empty" if items_received == 0 and skipped == 0 else "completed"
            advanced = False
            if mode == "incremental":
                advanced = _advance(checkpoints, key, config, completed, now())
            finished = result(status, advanced=advanced)
            _write_manifest(bronze, config, finished, started, now(), mode, quota_remaining)
            return finished
    except LocalLockConflict:
        raise
    except AnswerCollectionError as error:
        errors.append(str(error))
        failed = result("truncated" if truncated else "failed", advanced=False)
        try:
            _write_manifest(bronze, config, failed, started, now(), mode, quota_remaining)
        except Exception:
            LOGGER.info(
                json.dumps(
                    {"event": "answer_manifest_failure", "run_id": str(run_id), "status": "failed"},
                    sort_keys=True,
                )
            )
        return failed
    except Exception as error:
        errors.append(type(error).__name__)
        failed = result("failed", advanced=False)
        try:
            _write_manifest(bronze, config, failed, started, now(), mode, quota_remaining)
        except Exception:
            LOGGER.info(
                json.dumps(
                    {
                        "event": "answer_manifest_failure",
                        "run_id": str(run_id),
                        "status": "failed",
                    },
                    sort_keys=True,
                )
            )
        return failed


def _advance(
    checkpoints: LocalAnswerCheckpoint,
    key: str,
    config: AnswerCollectionConfig,
    completed: list[AnswerBatch],
    updated_at: datetime,
) -> bool:
    expected = checkpoints.read(key)
    previous_batches = () if expected is None else expected.completed_batch_ids
    previous_ids = () if expected is None else expected.completed_question_ids
    batches = tuple(sorted(set(previous_batches) | {batch.batch_id for batch in completed}))
    questions = tuple(
        sorted(
            set(previous_ids)
            | {question_id for batch in completed for question_id in batch.question_ids}
        )
    )
    checkpoints.update(
        expected,
        AnswerCheckpoint(
            source=SOURCE,
            site=config.site,
            endpoint=config.endpoint,
            strategy=STRATEGY,
            completed_batch_ids=batches,
            completed_question_ids=questions,
            updated_at=updated_at,
        ),
    )
    return True


def _page(
    config: AnswerCollectionConfig,
    batch: AnswerBatch,
    origin: str,
    run_id: UUID,
    page_number: int,
    requested_at: datetime,
    recovered_at: datetime,
    payload: dict[str, Any],
    params: dict[str, str | int],
    response: ApiResponse,
) -> AnswerBronzePage:
    items = payload.get("items")
    count = len(items) if isinstance(items, list) else 0
    return AnswerBronzePage(
        run_id=run_id,
        source=SOURCE,
        endpoint=batch.url,
        site=config.site,
        strategy=STRATEGY,
        batch_id=batch.batch_id,
        batch_index=batch.batch_index,
        question_ids=batch.question_ids,
        question_ids_origin=origin,
        page=page_number,
        requested_at=requested_at,
        recovered_at=recovered_at,
        payload=payload,
        request_params=params,
        has_more=response.has_more,
        quota_max=response.quota_max,
        quota_remaining=response.quota_remaining,
        backoff_seconds=response.backoff_seconds,
        http_status=200,
        collector_version=COLLECTOR_VERSION,
        user_agent=config.user_agent,
        item_count=count,
        truncated=False,
        checksum=payload_checksum(payload),
    )


def _account(payload: dict[str, Any], batch: AnswerBatch, seen: set[int]) -> tuple[int, int, int]:
    items = payload.get("items")
    if not isinstance(items, list):
        return 0, 0, 0
    received = 0
    duplicates = 0
    outside = 0
    allowed = set(batch.question_ids)
    for item in items:
        if not isinstance(item, dict):
            continue
        received += 1
        answer_id = item.get("answer_id")
        question_id = item.get("question_id")
        if type(answer_id) is int and answer_id > 0:
            if answer_id in seen:
                duplicates += 1
            seen.add(answer_id)
        if type(question_id) is int and question_id not in allowed:
            outside += 1
    return received, duplicates, outside


def _log_page(
    run_id: UUID,
    site: str,
    batch: AnswerBatch,
    page: int,
    received: int,
    response: ApiResponse,
) -> None:
    LOGGER.info(
        json.dumps(
            {
                "event": "answer_page",
                "run_id": str(run_id),
                "site": site,
                "endpoint": "questions/{ids}/answers",
                "batch_index": batch.batch_index,
                "question_id_count": len(batch.question_ids),
                "page": page,
                "items_received": received,
                "quota_remaining": response.quota_remaining,
                "has_more": response.has_more,
                "backoff_seconds": response.backoff_seconds,
                "attempts": response.attempts,
                "status": "page_persisted",
            },
            sort_keys=True,
        )
    )


def _write_manifest(
    bronze: LocalAnswerBronzeStore,
    config: AnswerCollectionConfig,
    result: AnswerCollectionResult,
    started: datetime,
    finished: datetime,
    mode: str,
    quota_remaining: int | None,
) -> None:
    record = to_record(result)
    record.update(
        schema_version="answers_collection_manifest.v1",
        source=SOURCE,
        site=config.site,
        endpoint=config.endpoint,
        strategy=STRATEGY,
        started_at=started.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        finished_at=finished.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        mode=mode,
        quota_remaining=quota_remaining,
        collector_version=COLLECTOR_VERSION,
    )
    LOGGER.info(
        json.dumps(
            {
                "event": "answer_run",
                "run_id": str(result.run_id),
                "site": config.site,
                "status": result.status,
                "batches_completed": result.batches_completed,
                "items_received": result.items_received,
                "duplicates": result.duplicates,
                "truncated": result.truncated,
                "mode": mode,
                "checkpoint_advanced": result.checkpoint_advanced,
            },
            sort_keys=True,
        )
    )
    bronze.write_manifest(result.run_id, record)
