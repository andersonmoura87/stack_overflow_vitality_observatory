"""Small synthetic Bronze fixtures for Silver tests; no external data."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from stackoverflow_vitality.domain.models import BronzePage, CollectionWindow
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.silver.models import InputPage, digest
from stackoverflow_vitality.silver.quality import QualityConfig, normalize
from stackoverflow_vitality.storage.bronze import LocalBronzeStore
from stackoverflow_vitality.storage.files import canonical

NOW = datetime(2026, 9, 21, tzinfo=UTC)
COLLECTED = datetime(2026, 9, 10, tzinfo=UTC)
SOURCE_ID = "00000000-0000-0000-0000-000000000001"
TRANSFORM_ID = "00000000-0000-0000-0000-000000000099"


def question(**changes):
    record = {
        "question_id": 123,
        "title": "  Synthetic &amp; question ",
        "body": "<p>Hello &amp; world.</p><pre><code>x = 1\n  print(x)\n</code></pre>",
        "tags": ["python", "testing", "python"],
        "creation_date": 1788220800,
        "last_activity_date": 1788307200,
        "score": -2,
        "view_count": 10,
        "answer_count": 1,
        "is_answered": True,
        "owner": {"user_id": 42},
        "content_license": "CC BY-SA 4.0",
        "link": "https://example.test/questions/123",
    }
    record.update(changes)
    return record


def page(items=None, collected=COLLECTED, run_id=SOURCE_ID, number=1, **changes):
    payload = {"items": [question()] if items is None else items, "has_more": False}
    snapshot = BronzePage(
        run_id=UUID(run_id),
        source="stackexchange_api",
        endpoint="https://api.stackexchange.com/2.3/questions",
        site="stackoverflow",
        tag="python",
        window=CollectionWindow(datetime(2026, 9, 1, tzinfo=UTC), collected),
        page=number,
        recovered_at=collected,
        payload=payload,
        request_params={"tagged": "python"},
        quota_max=10000,
        quota_remaining=9999,
        backoff_seconds=None,
        has_more=False,
        collector_version="0.2.0",
        schema_version="bronze_stackexchange_questions_snapshot.v2",
        checksum=BronzePage.payload_checksum(payload),
    )
    return replace(snapshot, **changes)


def input_page(snapshot=None):
    snapshot = snapshot or page()
    record = to_record(snapshot)
    return InputPage(
        f"run_id={snapshot.run_id}/page={snapshot.page:04d}.json",
        digest(canonical(record)),
        record,
        tuple(snapshot.payload["items"]),
    )


def normalized(snapshot=None, index=0):
    source = input_page(snapshot)
    result, events = normalize(
        source.items[index], source, index, TRANSFORM_ID, NOW, QualityConfig()
    )
    assert result is not None, events
    return result


def write_page(root: Path, snapshot=None):
    snapshot = snapshot or page()
    store = LocalBronzeStore(root)
    store.write_page(snapshot)
    return store.page_path(snapshot)
