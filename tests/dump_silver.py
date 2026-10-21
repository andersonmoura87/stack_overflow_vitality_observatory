"""Helpers for offline Data Dump Silver tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from stackoverflow_vitality.data_dump.ingest import ingest_data_dump
from stackoverflow_vitality.storage.files import canonical
from tests.dump_fixtures import clock, config, write_posts


def publish_bronze(
    root: Path, rows: list[str], question_ids: tuple[int, ...], **overrides: object
) -> tuple[Path, dict[str, Any]]:
    source = root / "Posts.xml"
    write_posts(source, rows)
    result = ingest_data_dump(
        config(root, **overrides),
        source,
        question_ids,
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    manifest = root / "bronze" / "data_dump" / "manifests" / f"{result['ingestion_identity']}.json"
    return manifest, result


def shard_path(manifest_path: Path) -> Path:
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    shard = document["shards"][0]
    return (
        manifest_path.parent.parent
        / f"site={document['site']}"
        / f"g={document['ingestion_identity']}"
        / shard["name"]
    )


def rewrite_shard(manifest_path: Path, mutate: Any) -> None:
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = shard_path(manifest_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mutate(rows)
    _store_shard(manifest_path, document, path, b"".join(canonical(row) + b"\n" for row in rows))


def rewrite_raw(manifest_path: Path, payload: bytes) -> None:
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    _store_shard(manifest_path, document, shard_path(manifest_path), payload)


def _store_shard(manifest_path: Path, document: dict[str, Any], path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    shard = document["shards"][0]
    shard["sha256"] = hashlib.sha256(payload).hexdigest()
    shard["bytes"] = len(payload)
    shard["records"] = payload.count(b"\n")
    manifest_path.write_bytes(canonical(document))
