from datetime import UTC, datetime
from uuid import uuid4

from stackoverflow_vitality.domain.models import (
    CollectionRequest,
    CollectionStatus,
    CollectionWindow,
)
from stackoverflow_vitality.ingestion.collector import collect_questions
from stackoverflow_vitality.ingestion.interfaces import ApiResponse
from stackoverflow_vitality.storage.memory import MemoryBronzeStore, MemoryWatermarkStore


class FakeHttp:
    def __init__(self, responses: list[ApiResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str | int]] = []

    def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse:
        self.calls.append(params)
        return self.responses[len(self.calls) - 1]


def make_request(max_pages: int = 3) -> CollectionRequest:
    return CollectionRequest(
        "stackoverflow",
        "https://api.stackexchange.com/2.3/questions",
        "python",
        CollectionWindow(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)),
        max_pages=max_pages,
    )


def test_persists_pages_manifest_and_commits_watermark() -> None:
    http = FakeHttp([ApiResponse([{"question_id": 1}], False, 100, 99)])
    bronze = MemoryBronzeStore()
    watermarks = MemoryWatermarkStore()
    run = collect_questions(make_request(), uuid4(), http, bronze, watermarks)
    assert run.status == CollectionStatus.COMPLETED
    assert len(bronze.pages) == 1
    assert len(bronze.manifests) == 1
    assert watermarks.read("stackexchange_api", "python") is not None
    assert http.calls[0]["tagged"] == "python"


def test_multiple_pages_obey_backoff_and_preserve_run_id() -> None:
    run_id = uuid4()
    http = FakeHttp(
        [
            ApiResponse([{"question_id": 1}], True, 100, 99, 2),
            ApiResponse([{"question_id": 2}], False, 100, 98),
        ]
    )
    bronze = MemoryBronzeStore()
    sleeps: list[float] = []
    run = collect_questions(
        make_request(), run_id, http, bronze, MemoryWatermarkStore(), sleeper=sleeps.append
    )
    assert run.status == "completed"
    assert [page.page for page in bronze.pages] == [1, 2]
    assert all(page.run_id == run_id for page in bronze.pages)
    assert sleeps == [2]


def test_empty_response_is_valid_and_advances_watermark() -> None:
    bronze = MemoryBronzeStore()
    watermarks = MemoryWatermarkStore()
    run = collect_questions(
        make_request(), uuid4(), FakeHttp([ApiResponse([], False)]), bronze, watermarks
    )
    assert run.status == "empty"
    assert run.items_received == 0
    assert watermarks.read("stackexchange_api", "python") is not None


def test_truncation_is_explicit_and_preserves_watermark() -> None:
    watermarks = MemoryWatermarkStore()
    run = collect_questions(
        make_request(max_pages=1),
        uuid4(),
        FakeHttp([ApiResponse([{"question_id": 1}], True)]),
        MemoryBronzeStore(),
        watermarks,
    )
    assert run.status == "truncated"
    assert run.truncated
    assert watermarks.read("stackexchange_api", "python") is None
    assert run.errors == ("PageLimitReached",)


def test_failure_after_page_keeps_snapshot_but_not_watermark() -> None:
    class BrokenManifestStore(MemoryBronzeStore):
        def confirm_manifest(self, manifest: object) -> bool:
            return False

    watermarks = MemoryWatermarkStore()
    run = collect_questions(
        make_request(),
        uuid4(),
        FakeHttp([ApiResponse([{"question_id": 1}], False)]),
        BrokenManifestStore(),
        watermarks,
    )
    assert run.status == "failed"
    assert run.watermark_after is None
    assert watermarks.read("stackexchange_api", "python") is None


def test_default_runtime_sleeper_is_real():
    import inspect
    import time

    assert inspect.signature(collect_questions).parameters["sleeper"].default is time.sleep


def test_backoff_is_consumed_even_when_page_write_fails():
    class BrokenBronze(MemoryBronzeStore):
        def write_page(self, page):
            raise OSError("disk failure")

    sleeps = []
    result = collect_questions(
        make_request(),
        uuid4(),
        FakeHttp([ApiResponse([], False, backoff_seconds=90)]),
        BrokenBronze(),
        MemoryWatermarkStore(),
        sleeper=sleeps.append,
    )
    assert result.status == "failed"
    assert sleeps == [90]


def test_quota_zero_with_more_pages_does_not_call_again():
    http = FakeHttp([ApiResponse([], True, quota_remaining=0)])
    watermarks = MemoryWatermarkStore()
    result = collect_questions(make_request(), uuid4(), http, MemoryBronzeStore(), watermarks)
    assert result.status == "failed"
    assert result.errors == ("QuotaExhausted",)
    assert len(http.calls) == 1
    assert watermarks.read("stackexchange_api", "python") is None


def test_start_page_uses_page_count_for_backfill():
    from dataclasses import replace

    request = replace(make_request(max_pages=2), start_page=5, backfill=True)
    http = FakeHttp([ApiResponse([], True), ApiResponse([], False)])
    result = collect_questions(request, uuid4(), http, MemoryBronzeStore(), MemoryWatermarkStore())
    assert result.status == "empty"
    assert [call["page"] for call in http.calls] == [5, 6]
