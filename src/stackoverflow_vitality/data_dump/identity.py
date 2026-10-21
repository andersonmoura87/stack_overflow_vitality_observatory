"""Canonical identities for one offline Data Dump generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from stackoverflow_vitality.storage.files import canonical

PARSER_VERSION = "1.0.0"
CONTRACT_VERSION = "bronze_stackexchange_data_dump_posts.v1"
MANIFEST_VERSION = "bronze_stackexchange_data_dump_posts_manifest.v1"
SOURCE_KIND = "stackexchange_data_dump"
MODE = "restricted"


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def selection_sha256(question_ids: tuple[int, ...]) -> str:
    encoded = json.dumps(
        sorted(question_ids), separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return digest(encoded)


def source_identity(site: str, source_sha256: str, source_file_size: int) -> str:
    payload = canonical(
        {"site": site, "source_sha256": source_sha256, "source_file_size": source_file_size}
    )
    return digest(payload)


def ingestion_identity(
    *,
    source: str,
    selection: str,
    max_attribute_bytes: int,
    max_body_chars: int,
    max_shard_records: int,
    max_shard_bytes: int,
    dump_publication_id: str,
    dump_cutoff: str | None,
    dump_cutoff_status: str,
) -> str:
    document: dict[str, Any] = {
        "source_identity": source,
        "contract_version": CONTRACT_VERSION,
        "parser_version": PARSER_VERSION,
        "mode": MODE,
        "selection_sha256": selection,
        "max_attribute_bytes": max_attribute_bytes,
        "max_body_chars": max_body_chars,
        "max_shard_records": max_shard_records,
        "max_shard_bytes": max_shard_bytes,
        "dump_publication_id": dump_publication_id,
        "dump_cutoff": dump_cutoff,
        "dump_cutoff_status": dump_cutoff_status,
    }
    return digest(canonical(document))
