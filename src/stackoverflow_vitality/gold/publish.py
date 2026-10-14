"""Publish an immutable descriptive Gold generation from a committed Silver dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stackoverflow_vitality.gold.metrics import Aggregation, aggregate, latest_collected_at
from stackoverflow_vitality.gold.models import (
    CAUSAL_CLAIMS,
    IDENTITY_FIELDS,
    LIMITATION,
    PUBLICATION_SCHEMA,
    TRANSFORMER_VERSION,
    stamp,
    validate_publication,
)
from stackoverflow_vitality.silver.models import SCHEMA_VERSION, digest
from stackoverflow_vitality.silver.storage import (
    LocalSilverStore,
    LocalTransformationManifestStore,
    write_immutable,
)
from stackoverflow_vitality.storage.files import ImmutableConflict, canonical, exclusive_lock


@dataclass(frozen=True)
class VitalityBuild:
    status: str
    publication_id: str
    as_of: str
    as_of_source: str
    questions_included: int
    questions_without_observation: int
    questions_created_after_as_of: int
    monthly_rows: int
    tag_rows: int
    descriptive_only: bool
    causal_claims: str
    limitation: str
    output_paths: dict[str, str]
    manifest_path: str | None


def build_vitality(
    silver_root: Path,
    gold_root: Path,
    as_of: datetime | None = None,
    *,
    dry_run: bool = False,
) -> VitalityBuild:
    silver = silver_root.resolve()
    gold = gold_root.resolve()
    _validate_roots(silver, gold)
    if as_of is None and not dry_run:
        raise ValueError("as_of is required to publish Gold")
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("as_of requires a UTC timestamp")
    store = LocalSilverStore(silver)
    manifest = LocalTransformationManifestStore(silver / "manifests").latest(store.dataset_id)
    if manifest is None:
        raise ValueError("no committed Silver generation")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported Silver schema")
    state = store.load(manifest)
    if as_of is None:
        resolved = latest_collected_at(state)
        source = "derived_max_collected_at"
    else:
        resolved = as_of.astimezone(UTC)
        source = "explicit"
    built = aggregate(state, resolved)
    monthly = _lines(list(built.monthly))
    tags = _lines(list(built.tag_monthly))
    checksums = {"monthly": digest(monthly), "tag_monthly": digest(tags)}
    identity = _identity(manifest, built, checksums, source, resolved)
    publication_id = digest(canonical(identity))
    directory = gold / "vitality" / f"generation={publication_id}"
    paths = {
        "monthly": str(directory / "monthly.jsonl"),
        "tag_monthly": str(directory / "tag_monthly.jsonl"),
    }
    manifest_path = gold / "manifests" / f"{publication_id}.json"
    publication = dict(identity)
    publication.update(publication_id=publication_id, output_paths=paths)
    validate_publication(publication)
    if digest(canonical({key: publication[key] for key in IDENTITY_FIELDS})) != publication_id:
        raise ValueError("publication identity drifted")
    result = VitalityBuild(
        status="dry_run" if dry_run else "completed",
        publication_id=publication_id,
        as_of=stamp(resolved),
        as_of_source=source,
        questions_included=built.questions_included,
        questions_without_observation=built.questions_without_observation,
        questions_created_after_as_of=built.questions_created_after_as_of,
        monthly_rows=len(built.monthly),
        tag_rows=len(built.tag_monthly),
        descriptive_only=True,
        causal_claims="unsupported",
        limitation=LIMITATION,
        output_paths={} if dry_run else paths,
        manifest_path=None if dry_run else str(manifest_path),
    )
    if dry_run:
        return result
    if source != "explicit":
        raise ValueError("as_of is required to publish Gold")
    payload = canonical(publication)
    with exclusive_lock(gold / ".gold.lock"):
        for name, data in (("monthly", monthly), ("tag_monthly", tags)):
            path = directory / f"{name}.jsonl"
            try:
                write_immutable(path, data)
            except ImmutableConflict as error:
                raise ImmutableConflict("Gold artifact differs") from error
            if path.read_bytes() != data:
                raise ValueError("Gold output confirmation failed")
        try:
            write_immutable(manifest_path, payload)
        except ImmutableConflict as error:
            raise ImmutableConflict("Gold artifact differs") from error
        if manifest_path.read_bytes() != payload:
            raise ValueError("Gold manifest confirmation failed")
    return result


def _identity(
    manifest: dict[str, Any],
    built: Aggregation,
    checksums: dict[str, str],
    source: str,
    as_of: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": PUBLICATION_SCHEMA,
        "transformer_version": TRANSFORMER_VERSION,
        "silver_schema_version": _text(manifest, "schema_version"),
        "silver_dataset_id": _text(manifest, "dataset_id"),
        "silver_commit_sequence": _count(manifest, "commit_sequence"),
        "silver_transformation_run_id": _text(manifest, "transformation_run_id"),
        "silver_input_records": _count(manifest, "input_records"),
        "silver_accepted_records": _count(manifest, "accepted_records"),
        "silver_quarantined_records": _count(manifest, "quarantined_records"),
        "silver_quality_errors": _count(manifest, "quality_errors"),
        "silver_quality_warnings": _count(manifest, "quality_warnings"),
        "as_of": stamp(as_of),
        "as_of_source": source,
        "questions_included": built.questions_included,
        "questions_without_observation": built.questions_without_observation,
        "questions_created_after_as_of": built.questions_created_after_as_of,
        "monthly_rows": len(built.monthly),
        "tag_rows": len(built.tag_monthly),
        "output_checksums": checksums,
        "descriptive_only": True,
        "causal_claims": CAUSAL_CLAIMS,
        "limitation": LIMITATION,
    }


def _lines(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical(record) + b"\n" for record in records)


def _validate_roots(silver: Path, gold: Path) -> None:
    if not silver.is_dir():
        raise ValueError("Silver path must be an existing directory")
    for root in (silver, gold):
        if any(path.exists() and not path.is_dir() for path in (root, *root.parents)):
            raise ValueError("all paths and ancestors must be directories")
    if silver == gold or silver.is_relative_to(gold) or gold.is_relative_to(silver):
        raise ValueError("Silver and Gold roots must not overlap")


def _text(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid Silver manifest field {key}")
    return value


def _count(record: dict[str, Any], key: str) -> int:
    value = record.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid Silver manifest field {key}")
    return value
