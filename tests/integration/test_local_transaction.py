"""Offline HTTP -> two pages -> verified Bronze -> CAS -> terminal manifest."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from stackoverflow_vitality.domain.models import Watermark
from stackoverflow_vitality.ingestion.collector import collect_questions
from stackoverflow_vitality.ingestion.http import HttpxStackExchangeClient
from stackoverflow_vitality.storage.bronze import LocalBronzeStore, LocalWatermarkStore
from tests.unit.test_collector import make_request

NOW = datetime(2026, 9, 16, tzinfo=UTC)


def client_for(sleeps, fail_page=None):
    def handler(request):
        number = int(request.url.params["page"])
        if number == fail_page:
            raise httpx.ReadTimeout("private-secret", request=request)
        return httpx.Response(
            200,
            json={
                "items": [{"question_id": number, "title": "Synthetic question"}],
                "has_more": number == 1,
                "quota_remaining": 100 - number,
                "backoff": 70 if number == 1 else 0,
            },
        )

    return HttpxStackExchangeClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=sleeps.append,
        jitter=lambda base: 0,
        max_attempts=1,
    )


def execute(tmp_path, *, bronze=None, watermarks=None, fail_page=None, request=None, replay=()):
    sleeps = []
    bronze = bronze or LocalBronzeStore(tmp_path)
    watermarks = watermarks or LocalWatermarkStore(tmp_path / "watermarks.json")
    client = client_for(sleeps, fail_page)
    try:
        result = collect_questions(
            request or make_request(),
            uuid4(),
            client,
            bronze,
            watermarks,
            now=lambda: NOW,
            sleeper=sleeps.append,
            replay_pages=replay,
        )
    finally:
        client.close()
    return result, sleeps, bronze, watermarks


def test_two_pages_same_directory_verified_before_terminal(tmp_path):
    result, sleeps, _, watermarks = execute(tmp_path)
    assert result.status == "completed"
    assert sleeps == [70]
    pages = list(tmp_path.rglob("page=*.json"))
    terminal = next(tmp_path.rglob("manifest.json"))
    prepared = next(tmp_path.rglob("prepared.json"))
    assert len(pages) == 2
    assert all(path.parent == terminal.parent == prepared.parent for path in pages)
    assert "window_date=2025-01-01" in str(terminal)
    from stackoverflow_vitality.domain.models import BronzePage

    for path in pages:
        record = json.loads(path.read_bytes())
        assert record["checksum"] == BronzePage.payload_checksum(record["payload"])
    assert json.loads(prepared.read_bytes())["status"] == "pages_persisted"
    assert json.loads(prepared.read_bytes())["watermark_after"] is None
    assert json.loads(terminal.read_bytes())["status"] == "completed"
    assert watermarks.read("stackexchange_api", "python").value == make_request().window.end


@pytest.mark.parametrize(
    "boundary",
    ["http", "page_write", "page_integrity", "manifest_prepare", "watermark", "manifest_terminal"],
)
def test_failure_at_each_boundary_is_observable_without_secret(tmp_path, boundary, caplog):
    class BrokenBronze(LocalBronzeStore):
        def write_page(self, page):
            if boundary == "page_write" and page.page == 2:
                raise OSError("private-secret")
            super().write_page(page)

        def confirm_page(self, page):
            return False if boundary == "page_integrity" else super().confirm_page(page)

        def write_manifest(self, manifest):
            if boundary == "manifest_prepare" and manifest.status == "pages_persisted":
                raise OSError("private-secret")
            if boundary == "manifest_terminal" and manifest.status == "completed":
                raise OSError("private-secret")
            super().write_manifest(manifest)

    class BrokenWatermark(LocalWatermarkStore):
        def update(self, previous, new):
            if boundary == "watermark":
                raise OSError("private-secret")
            super().update(previous, new)

    result, _, _, watermarks = execute(
        tmp_path,
        bronze=BrokenBronze(tmp_path),
        watermarks=BrokenWatermark(tmp_path / "watermarks.json"),
        fail_page=2 if boundary == "http" else None,
    )
    assert result.status == "failed"
    assert result.failure_stage == boundary
    assert "private-secret" not in caplog.text
    assert "private-secret" not in str(result.errors)
    terminals = list(tmp_path.rglob("manifest.json"))
    if boundary == "manifest_terminal":
        assert not terminals
        assert list(tmp_path.rglob("prepared.json"))
        assert watermarks.read("stackexchange_api", "python") is not None
        assert result.watermark_after is not None
    else:
        assert json.loads(terminals[0].read_bytes())["status"] == "failed"
        assert json.loads(terminals[0].read_bytes())["failure_stage"] == boundary
        assert watermarks.read("stackexchange_api", "python") is None


def test_backfill_does_not_advance_or_shrink_window(tmp_path):
    store = LocalWatermarkStore(tmp_path / "watermarks.json")
    previous = Watermark("stackexchange_api", "python", NOW, NOW)
    store.update(None, previous)
    request = replace(make_request(), backfill=True)
    result, _, _, _ = execute(tmp_path, watermarks=store, request=request)
    assert result.status == "completed"
    assert result.request.window == request.window
    assert result.watermark_after is None
    assert store.read("stackexchange_api", "python") == previous
    assert json.loads(next(tmp_path.rglob("manifest.json")).read_bytes())["backfill"] is True


def test_older_operational_window_fails_before_http(tmp_path):
    store = LocalWatermarkStore(tmp_path / "watermarks.json")
    previous = Watermark("stackexchange_api", "python", NOW, NOW)
    store.update(None, previous)
    result, sleeps, _, _ = execute(tmp_path, watermarks=store)
    assert result.status == "failed"
    assert result.failure_stage == "planning"
    assert not sleeps
    assert not list(tmp_path.rglob("page=*.json"))
    assert store.read("stackexchange_api", "python") == previous


def test_overlap_uses_confirmed_watermark_and_manifest_has_effective_window(tmp_path):
    store = LocalWatermarkStore(tmp_path / "watermarks.json")
    request = make_request()
    previous = Watermark("stackexchange_api", "python", request.window.end, NOW)
    store.update(None, previous)
    request = replace(request, overlap_seconds=60)
    result, _, _, _ = execute(tmp_path, watermarks=store, request=request)
    assert result.status == "completed"
    assert result.request.window.start == previous.value - timedelta(seconds=60)
    manifest = json.loads(next(tmp_path.rglob("manifest.json")).read_bytes())
    assert manifest["window_start"] == "2025-01-01T23:59:00Z"


def test_retry_can_reuse_identical_verified_bronze_after_watermark_failure(tmp_path):
    class CaptureBronze(LocalBronzeStore):
        def __init__(self, root):
            super().__init__(root)
            self.pages = []

        def write_page(self, page):
            self.pages.append(page)
            super().write_page(page)

    class FailedWatermark(LocalWatermarkStore):
        def update(self, previous, new):
            raise OSError("write failed")

    bronze = CaptureBronze(tmp_path)
    first, _, _, _ = execute(
        tmp_path,
        bronze=bronze,
        watermarks=FailedWatermark(tmp_path / "watermarks.json"),
    )
    assert first.status == "failed"
    old_pages = tuple(bronze.pages)
    originals = {bronze.page_path(page): bronze.page_path(page).read_bytes() for page in old_pages}
    second, _, _, _ = execute(tmp_path, bronze=bronze, fail_page=1, replay=old_pages)
    assert second.status == "completed"
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert all(page.replayed_from_run_id == first.run_id for page in bronze.pages[2:])
