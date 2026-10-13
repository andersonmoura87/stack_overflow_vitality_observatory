"""Deterministic synthetic questions: reserved namespace, no people or external data."""

import hashlib
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from benchmarks.config import Config
from stackoverflow_vitality.domain.models import BronzePage, CollectionWindow
from stackoverflow_vitality.storage.bronze import LocalBronzeStore

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
NOW = EPOCH + timedelta(days=30)
QuestionValue = str | int | bool | list[str]
QuestionRow = dict[str, QuestionValue]


class CorpusInfo(TypedDict):
    bytes: int
    pages: int
    sha256: str


def _text(row: QuestionRow, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"synthetic {key} must be text")
    return value


def _integer(row: QuestionRow, key: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise TypeError(f"synthetic {key} must be an integer")
    return value


def selected(config: Config, name: str, rate: float) -> set[int]:
    rng = random.Random(f"{config.seed}:{name}")
    return set(rng.sample(range(config.unique_records), int(config.unique_records * rate)))


def question(config: Config, index: int) -> QuestionRow:
    rng = random.Random(f"{config.seed}:question:{index}")
    token = rng.randrange(10**9)
    blocks = rng.randint(0, 1) if config.profile == "small_text" else rng.randint(1, 3)
    if config.profile == "large_question":
        blocks = 12
    code = "".join(
        f"<pre><code>value_{i} = {token}\nif value_{i}:\n    print('&lt;synthetic&gt;')\n"
        f"# SyntheticError: fixture {index}\n</code></pre>"
        for i in range(blocks)
    )
    paragraph = (
        f"<section><p>Synthetic fixture {index}: token {token} &amp; state &lt;ready&gt;.</p>"
        "<p>Expected a synthetic result; observed SyntheticError. "
        '<a href="https://example.invalid/reference">Fixture reference</a>.</p></section>'
    )
    body = paragraph + code
    if config.profile == "large_question":
        target = int(config.max_body_chars * 0.9)
        body = "<div>" + paragraph * max(1, (target - len(code) - 11) // len(paragraph)) + code
        body += "</div>"
    count = rng.randint(1, 3) if config.profile == "small_text" else rng.randint(2, 5)
    return {
        "question_id": 10**12 + index,
        "title": f"Synthetic fixture {token}"
        if config.profile == "small_text"
        else f"Synthetic state transition fails with token {token} &amp; generated fixture {index}",
        "body": body,
        "tags": rng.sample(["synthetic", "fixture", "parser", "state", "offline"], count),
        "creation_date": int(EPOCH.timestamp()),
        "last_activity_date": int((EPOCH + timedelta(days=1)).timestamp()),
        "score": rng.randint(-3, 20),
        "view_count": rng.randint(0, 1000),
        "answer_count": 1,
        "is_answered": True,
        "link": f"https://example.invalid/synthetic/{index}",
        # No owner or user identifiers are generated, even when optionals are present.
    }


def corpus(config: Config, variant: str = "base") -> Iterator[tuple[QuestionRow, bool]]:
    """Yield one page-sized stream; exact floor-rounded rates over unique questions."""
    groups = {
        name: selected(config, name, getattr(config, name + "_rate"))
        for name in ("unanswered", "closed", "missing_optional", "content_change", "metrics_change")
    }
    invalid = set(
        random.Random(f"{config.seed}:invalid").sample(
            range(config.records), int(config.records * config.invalid_rate)
        )
    )
    old = set(
        random.Random(f"{config.seed}:old").sample(
            range(config.records), int(config.records * config.out_of_order_rate)
        )
    )
    for position in range(config.records):
        index = position % config.unique_records
        row = question(config, index)
        if index in groups["unanswered"]:
            row.update(answer_count=0, is_answered=False)
        if index in groups["closed"]:
            row.update(
                closed_date=int((EPOCH + timedelta(days=2)).timestamp()),
                closed_reason="synthetic closure",
            )
        if index in groups["missing_optional"]:
            for field in ("link", "last_activity_date", "score"):
                row.pop(field, None)
        if variant == "metrics" and index in groups["metrics_change"]:
            score = row.get("score") or 0
            if type(score) is not int:
                raise TypeError("synthetic score must be an integer")
            row.update(
                score=score + 1,
                view_count=_integer(row, "view_count") + 7,
                answer_count=_integer(row, "answer_count") + 1,
            )
        if variant == "content" and index in groups["content_change"]:
            row["title"] = _text(row, "title") + " revised synthetic content"
        if variant == "invalid" and position in invalid:
            kind = position % 4
            if kind == 0:
                row.pop("question_id")
            elif kind == 1:
                row["tags"] = "invalid synthetic tags"
            elif kind == 2:
                row["view_count"] = -1
            else:
                row["creation_date"] = "invalid epoch"
        older = variant == "out_of_order" and position in old
        if older:
            row["title"] = _text(row, "title") + " earlier synthetic revision"
        yield row, older


def write_corpus(config: Config, root: Path, variant: str = "base") -> CorpusInfo:
    """Write valid immutable Bronze envelopes; deliberately invalid items remain parseable."""
    store = LocalBronzeStore(root)
    run = uuid5(NAMESPACE_URL, f"synthetic:{config.seed}:{config.records}:{variant}")
    pending: list[QuestionRow] = []
    number = 0
    size = 0
    fingerprint = hashlib.sha256()

    def flush(items: list[QuestionRow], older: bool) -> None:
        nonlocal number, size
        number += 1
        day = 5 if older else (10 if variant == "base" else 20)
        collected = EPOCH + timedelta(days=day)
        payload = {"items": items, "has_more": False}
        page = BronzePage(
            run_id=run,
            source="synthetic_benchmark",
            endpoint="https://example.invalid/questions",
            site="synthetic",
            tag="synthetic",
            window=CollectionWindow(EPOCH, collected),
            page=number,
            recovered_at=collected,
            payload=payload,
            request_params={"tagged": "synthetic"},
            quota_max=None,
            quota_remaining=None,
            backoff_seconds=None,
            has_more=False,
            collector_version="benchmark.1",
            schema_version="bronze_stackexchange_questions_snapshot.v2",
            checksum=BronzePage.payload_checksum(payload),
        )
        store.write_page(page)
        data = store.page_path(page).read_bytes()
        size += len(data)
        fingerprint.update(data)

    previous = False
    for item, older in corpus(config, variant):
        if pending and (older != previous or len(pending) == 100):
            flush(pending, previous)
            pending = []
        pending.append(item)
        previous = older
    if pending:
        flush(pending, previous)
    return {"bytes": size, "pages": number, "sha256": fingerprint.hexdigest()}
