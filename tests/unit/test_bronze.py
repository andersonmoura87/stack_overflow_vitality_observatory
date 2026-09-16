import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from stackoverflow_vitality.domain.models import BronzePage, CollectionStatus, CollectionWindow
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.ingestion.collector import collect_questions
from stackoverflow_vitality.ingestion.interfaces import ApiResponse
from stackoverflow_vitality.storage.bronze import LocalBronzeStore, LocalWatermarkStore


def page(payload: dict[str, object] | None = None) -> BronzePage:
    body = payload or {"items": [{"question_id": 1}], "has_more": False}
    return BronzePage(
        run_id=uuid4(),
        source="stackexchange_api",
        endpoint="https://api.stackexchange.com/2.3/questions",
        site="stackoverflow",
        tag="python",
        window=CollectionWindow(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)),
        page=1,
        recovered_at=datetime(2026, 9, 2, tzinfo=UTC),
        payload=body,
        request_params={"site": "stackoverflow", "tagged": "python", "page": 1},
        quota_max=10000,
        quota_remaining=9999,
        backoff_seconds=None,
        has_more=False,
        collector_version="0.2.0",
        schema_version="bronze_stackexchange_questions_snapshot.v1",
        checksum=BronzePage.payload_checksum(body),
    )


def test_checksum_is_deterministic_and_payload_is_preserved(tmp_path: Path) -> None:
    stored = page({"has_more": False, "items": [{"question_id": 1, "title": "raw"}]})
    store = LocalBronzeStore(tmp_path)
    store.write_page(stored)
    path = next(tmp_path.rglob("page=0001.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["payload"]["items"][0]["title"] == "raw"
    assert record["checksum"] == BronzePage.payload_checksum(record["payload"])
    assert not list(path.parent.glob(".*"))


def test_manifest_and_watermark_are_written_atomically(tmp_path: Path) -> None:
    class FakeHttp:
        def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse:
            return ApiResponse([{"question_id": 1}], False, 10000, 9999)

    from stackoverflow_vitality.domain.models import CollectionRequest

    request = CollectionRequest(
        "stackoverflow",
        "https://api.stackexchange.com/2.3/questions",
        "python",
        CollectionWindow(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)),
    )
    bronze = LocalBronzeStore(tmp_path / "bronze")
    watermarks = LocalWatermarkStore(tmp_path / "watermarks.json")
    run = collect_questions(request, uuid4(), FakeHttp(), bronze, watermarks)
    assert run.status == CollectionStatus.COMPLETED
    manifest = next((tmp_path / "bronze").rglob("manifest.json"))
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "completed"
    assert watermarks.read("stackexchange_api", "python") is not None
    assert not list(manifest.parent.glob(".*"))


def test_serialized_manifest_contains_required_fields(tmp_path: Path) -> None:
    stored = page()
    from stackoverflow_vitality.domain.models import ExecutionManifest

    manifest = ExecutionManifest(
        run_id=stored.run_id,
        source=stored.source,
        endpoint=stored.endpoint,
        site=stored.site,
        tag=stored.tag,
        window_start=stored.window.start,
        window_end=stored.window.end,
        started_at=stored.recovered_at,
        finished_at=stored.recovered_at,
        status=CollectionStatus.EMPTY,
        pages_requested=1,
        pages_persisted=1,
        items_received=0,
        quota_remaining=1,
        truncated=False,
        watermark_before=None,
        watermark_after=stored.window.end,
        collector_version="0.2.0",
    )
    record = to_record(manifest)
    assert {
        "run_id",
        "source",
        "endpoint",
        "site",
        "tag",
        "window_start",
        "window_end",
        "started_at",
        "finished_at",
        "status",
        "pages_requested",
        "pages_persisted",
        "items_received",
        "quota_remaining",
        "truncated",
        "watermark_before",
        "watermark_after",
        "collector_version",
        "errors",
    } <= set(record)
