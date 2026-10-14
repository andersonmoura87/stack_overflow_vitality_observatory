"""Deterministic question-id batches for /questions/{ids}/answers.

The semicolon joins vectorized ids. It is not the tag AND operator.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AnswerBatch:
    batch_index: int
    batch_id: str
    question_ids: tuple[int, ...]
    url: str

    def __post_init__(self) -> None:
        if self.batch_index < 0:
            raise ValueError("batch_index must be non-negative")
        if not self.question_ids:
            raise ValueError("batch must contain question ids")
        if self.question_ids != tuple(sorted(set(self.question_ids))):
            raise ValueError("batch ids must be unique and sorted")


def normalize_question_ids(raw: object) -> tuple[int, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("question id list must be a nonempty JSON array")
    seen: set[int] = set()
    for item in raw:
        if type(item) is not int or item < 1:
            raise ValueError("question ids must be unique positive integers")
        if item in seen:
            raise ValueError("question ids must be unique positive integers")
        seen.add(item)
    return tuple(sorted(seen))


def load_question_ids(payload: object) -> tuple[int, ...]:
    if isinstance(payload, dict):
        if set(payload) != {"question_ids"}:
            raise ValueError("question id document must be an array or {question_ids}")
        payload = payload["question_ids"]
    return normalize_question_ids(payload)


def batch_id_for(question_ids: tuple[int, ...]) -> str:
    encoded = json.dumps(list(question_ids), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def answers_url(endpoint: str, question_ids: tuple[int, ...]) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("endpoint must use HTTPS without credentials, query or fragment")
    if not parsed.path.rstrip("/").endswith("/questions"):
        raise ValueError("answers endpoint must be the /questions collection URL")
    joined = ";".join(str(question_id) for question_id in question_ids)
    return f"{endpoint.rstrip('/')}/{joined}/answers"


def plan_batches(
    question_ids: tuple[int, ...],
    *,
    endpoint: str,
    max_batch_size: int,
    max_url_length: int,
    query_allowance: int,
) -> tuple[AnswerBatch, ...]:
    if type(max_batch_size) is not int or max_batch_size < 1:
        raise ValueError("max_batch_size must be a positive integer")
    if type(max_url_length) is not int or max_url_length < 32:
        raise ValueError("max_url_length must be an integer of at least 32")
    if type(query_allowance) is not int or query_allowance < 0:
        raise ValueError("query_allowance must be a non-negative integer")
    if not question_ids:
        raise ValueError("batch must contain question ids")
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("question ids must be unique")
    question_ids = tuple(sorted(question_ids))
    batches: list[AnswerBatch] = []
    current: list[int] = []

    def flush() -> None:
        if not current:
            return
        ids = tuple(current)
        url = answers_url(endpoint, ids)
        batches.append(
            AnswerBatch(
                batch_index=len(batches),
                batch_id=batch_id_for(ids),
                question_ids=ids,
                url=url,
            )
        )
        current.clear()

    for question_id in question_ids:
        candidate = tuple([*current, question_id])
        url = answers_url(endpoint, candidate)
        fits = len(url) + 1 + query_allowance <= max_url_length
        if current and (len(candidate) > max_batch_size or not fits):
            flush()
            candidate = (question_id,)
            url = answers_url(endpoint, candidate)
            fits = len(url) + 1 + query_allowance <= max_url_length
        if not fits or len(candidate) > max_batch_size:
            raise ValueError("question id does not fit in a single batch URL")
        current.append(question_id)
    flush()
    if not batches:
        raise ValueError("batch must contain question ids")
    return tuple(batches)
