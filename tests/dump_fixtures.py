"""Shared Posts.xml fixtures for offline Data Dump tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from stackoverflow_vitality.data_dump.config import DumpIngestConfig

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def config(tmp_path: Path, **overrides: object) -> DumpIngestConfig:
    values: dict[str, object] = {
        "site": "stackoverflow",
        "publication_id": "2026-03",
        "bronze_output": str(tmp_path / "bronze"),
        "quarantine_output": str(tmp_path / "quarantine"),
        "min_free_bytes": 1,
        "max_records": 100,
        "max_file_bytes": 1024**2,
        "max_output_bytes": 1024**2,
        "progress_every": 100_000,
    }
    values.update(overrides)
    loaded = DumpIngestConfig(**values)  # type: ignore[arg-type]
    loaded.validate()
    return loaded


def question(post_id: int, **extra: str) -> str:
    attributes = {
        "Id": str(post_id),
        "PostTypeId": "1",
        "Body": "Question body",
        "CreationDate": "2020-01-02T03:04:05",
        "Score": "3",
        "Title": "title",
        "Tags": "<python><xml>",
        "OwnerUserId": "7",
        "ContentLicense": "CC BY-SA 4.0",
        "ViewCount": "11",
        "AnswerCount": "1",
        "CommentCount": "2",
        "AcceptedAnswerId": "8",
    }
    attributes.update(extra)
    return _render(attributes)


def answer(post_id: int, parent_id: int, **extra: str) -> str:
    attributes = {
        "Id": str(post_id),
        "PostTypeId": "2",
        "ParentId": str(parent_id),
        "Body": "Answer body",
        "CreationDate": "2020-01-03T03:04:05",
        "Score": "1",
        "OwnerUserId": "9",
        "ContentLicense": "CC BY-SA 4.0",
    }
    attributes.update(extra)
    return _render(attributes)


def write_posts(path: Path, rows: list[str]) -> None:
    body = "\n".join(f"<row {row} />" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n<posts>\n{body}\n</posts>\n',
        encoding="utf-8",
    )


def _render(attributes: dict[str, str]) -> str:
    return " ".join(f'{key}="{_escape(value)}"' for key, value in attributes.items())


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
