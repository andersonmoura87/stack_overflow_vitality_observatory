"""Incremental collection with immutable preparation and terminal manifests."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from stackoverflow_vitality.domain.models import (
    BronzePage,
    CollectionRequest,
    CollectionStatus,
    CollectionWindow,
    ExecutionManifest,
    IngestionRun,
    Watermark,
)
from stackoverflow_vitality.ingestion.interfaces import (
    ApiResponse,
    BronzePageStore,
    HttpClient,
    WatermarkStore,
)

LOGGER = logging.getLogger(__name__)
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


class PageLimitReached(RuntimeError):
    """The source still has data after the configured page count."""


class QuotaExhausted(RuntimeError):
    """Further pages are required but the source quota is exhausted."""


def collect_questions(
    request: CollectionRequest,
    run_id: UUID,
    http: HttpClient,
    bronze: BronzePageStore,
    watermarks: WatermarkStore,
    *,
    now: Clock = lambda: datetime.now(UTC),
    sleeper: Sleeper = time.sleep,
    collector_version: str = "0.2.0",
    replay_pages: tuple[BronzePage, ...] = (),
) -> IngestionRun:
    """Prepare -> CAS watermark -> terminal. No cross-file transaction is claimed."""
    started = now()
    window = request.window
    watermark: Watermark | None = None
    watermark_after: datetime | None = None
    pages: list[BronzePage] = []
    pages_requested = 0
    items_received = 0
    quota_remaining: int | None = None
    errors: list[str] = []
    status = CollectionStatus.FAILED
    stage = "planning"
    failure_stage: str | None = None
    page = request.start_page

    def manifest(state: CollectionStatus) -> ExecutionManifest:
        return ExecutionManifest(
            run_id=run_id,
            source="stackexchange_api",
            endpoint=request.endpoint,
            site=request.site,
            tag=request.tag_or_group,
            window_start=window.start,
            window_end=window.end,
            started_at=started,
            finished_at=now(),
            status=state,
            pages_requested=pages_requested,
            pages_persisted=len(pages),
            items_received=items_received,
            quota_remaining=quota_remaining,
            truncated=status == CollectionStatus.TRUNCATED,
            watermark_before=watermark.value if watermark else None,
            watermark_after=watermark_after,
            collector_version=collector_version,
            errors=tuple(errors),
            failure_stage=failure_stage,
            backfill=request.backfill,
        )

    try:
        watermark = watermarks.read(
            "stackexchange_api",
            request.tag_or_group,
            site=request.site,
            endpoint=request.endpoint,
        )
        if watermark is not None and not request.backfill:
            if window.end < watermark.value:
                raise ValueError("window_end precedes operational watermark; use backfill")
            window = CollectionWindow(
                max(window.start, watermark.value - timedelta(seconds=request.overlap_seconds)),
                window.end,
            )
        effective = replace(request, window=window)
        replay = {snapshot.page: snapshot for snapshot in replay_pages}
        if len(replay) != len(replay_pages):
            raise ValueError("duplicate replay page")
        for snapshot in replay_pages:
            if (
                snapshot.window != window
                or snapshot.site != request.site
                or snapshot.tag != request.tag_or_group
                or snapshot.endpoint != request.endpoint
                or snapshot.run_id == run_id
                or snapshot.source != "stackexchange_api"
                or not bronze.confirm_page(snapshot)
            ):
                raise ValueError("replay context or integrity mismatch")
        while True:
            stage = "http"
            parameters: dict[str, str | int] = {
                "site": request.site,
                "tagged": request.tag_or_group,
                "fromdate": int(window.start.timestamp()),
                "todate": int(window.end.timestamp()),
                "page": page,
                "pagesize": request.page_size,
                "filter": request.api_filter,
            }
            pages_requested += 1
            prior = replay.get(page)
            if prior is None:
                response = http.get(request.endpoint, parameters)
            else:
                stage = "replay_integrity"
                if dict(prior.request_params) != parameters:
                    raise ValueError("replay request parameters differ")
                response = ApiResponse(
                    items=list(prior.payload["items"]),
                    has_more=prior.has_more,
                    quota_max=prior.quota_max,
                    quota_remaining=prior.quota_remaining,
                    backoff_seconds=prior.backoff_seconds,
                    payload=dict(prior.payload),
                    attempts=0,
                )
            # Consume the source obligation before a persistence error can abort this run.
            if response.backoff_seconds:
                stage = "api_backoff"
                sleeper(response.backoff_seconds)
            quota_remaining = response.quota_remaining
            payload = response.payload or {"items": response.items, "has_more": response.has_more}
            snapshot = BronzePage(
                run_id=run_id,
                source="stackexchange_api",
                endpoint=request.endpoint,
                site=request.site,
                tag=request.tag_or_group,
                window=window,
                page=page,
                recovered_at=prior.recovered_at if prior else now(),
                payload=payload,
                request_params=parameters,
                quota_max=response.quota_max,
                quota_remaining=response.quota_remaining,
                backoff_seconds=response.backoff_seconds,
                has_more=response.has_more,
                collector_version=collector_version,
                schema_version="bronze_stackexchange_questions_snapshot.v2",
                checksum=BronzePage.payload_checksum(payload),
                replayed_from_run_id=prior.run_id if prior else None,
            )
            stage = "page_write"
            bronze.write_page(snapshot)
            stage = "page_integrity"
            if not bronze.confirm_page(snapshot):
                raise RuntimeError("Bronze integrity confirmation failed")
            pages.append(snapshot)
            items_received += len(response.items)
            _log_event(
                logging.INFO,
                event="collection_page",
                run_id=run_id,
                request=effective,
                page=page,
                attempt=response.attempts,
                items_received=len(response.items),
                quota_remaining=quota_remaining,
                has_more=response.has_more,
                backoff_seconds=response.backoff_seconds,
                duration_ms=response.duration_ms,
                status="collecting",
            )
            if not response.has_more:
                break
            stage = "pagination"
            if pages_requested >= request.max_pages:
                raise PageLimitReached("has_more=true at configured page count")
            if quota_remaining == 0:
                raise QuotaExhausted("additional pages require quota")
            page += 1
        stage = "page_integrity"
        if not all(bronze.confirm_page(snapshot) for snapshot in pages):
            raise RuntimeError("Bronze integrity changed before commit")
        stage = "manifest_prepare"
        prepared = manifest(CollectionStatus.PAGES_PERSISTED)
        bronze.write_manifest(prepared)
        if not bronze.confirm_manifest(prepared):
            raise RuntimeError("prepared manifest integrity confirmation failed")
        stage = "watermark"
        if not request.backfill:
            watermarks.update(
                watermark,
                Watermark(
                    "stackexchange_api",
                    request.tag_or_group,
                    window.end,
                    now(),
                    site=request.site,
                    endpoint=request.endpoint,
                ),
            )
            watermark_after = window.end
        status = CollectionStatus.EMPTY if items_received == 0 else CollectionStatus.COMPLETED
    except Exception as error:
        status = (
            CollectionStatus.TRUNCATED
            if isinstance(error, PageLimitReached)
            else CollectionStatus.FAILED
        )
        failure_stage = stage
        errors.append(type(error).__name__)
        _log_event(
            logging.ERROR,
            event="collection_failed",
            run_id=run_id,
            request=replace(request, window=window),
            page=page,
            status=status.value,
            failure_stage=stage,
            error_type=type(error).__name__,
        )

    terminal = manifest(status)
    try:
        bronze.write_manifest(terminal)
        if not bronze.confirm_manifest(terminal):
            raise RuntimeError("terminal manifest integrity confirmation failed")
    except Exception as error:
        # A failed terminal publication can leave prepared.json + committed watermark.
        # Never roll back a watermark or overwrite an immutable manifest here.
        status = CollectionStatus.FAILED
        failure_stage = "manifest_terminal"
        errors.append(type(error).__name__)
        _log_event(
            logging.ERROR,
            event="terminal_publication_failed",
            run_id=run_id,
            request=replace(request, window=window),
            status=status.value,
            failure_stage=failure_stage,
            error_type=type(error).__name__,
        )
    return IngestionRun(
        run_id=run_id,
        request=replace(request, window=window),
        started_at=started,
        finished_at=now(),
        status=status.value,
        items_received=items_received,
        pages_received=len(pages),
        truncated=terminal.truncated,
        quota_remaining=quota_remaining,
        errors=tuple(errors),
        watermark_before=watermark.value if watermark else None,
        watermark_after=watermark_after,
        failure_stage=failure_stage,
    )


def _log_event(
    level: int,
    *,
    event: str,
    run_id: UUID,
    request: CollectionRequest,
    **fields: object,
) -> None:
    record: dict[str, object] = {
        "event": event,
        "run_id": str(run_id),
        "source": "stackexchange_api",
        "endpoint": request.endpoint,
        "site": request.site,
        "tag": request.tag_or_group,
        "window_start": request.window.start.isoformat(),
        "window_end": request.window.end.isoformat(),
        "page": None,
        "attempt": None,
        "items_received": None,
        "quota_remaining": None,
        "has_more": None,
        "backoff_seconds": None,
        "duration_ms": None,
        "status": None,
        "error_type": None,
    }
    record.update(fields)
    LOGGER.log(level, json.dumps(record, sort_keys=True))
