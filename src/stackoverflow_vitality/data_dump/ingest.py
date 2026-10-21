"""Offline ingest of a local Posts.xml into bronze_stackexchange_data_dump_posts.v1."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stackoverflow_vitality.answers.batching import load_question_ids
from stackoverflow_vitality.data_dump.config import DumpIngestConfig
from stackoverflow_vitality.data_dump.dates import parse_dump_instant, stamp
from stackoverflow_vitality.data_dump.errors import DumpAbort
from stackoverflow_vitality.data_dump.identity import (
    MANIFEST_VERSION,
    MODE,
    ingestion_identity,
    selection_sha256,
    source_identity,
)
from stackoverflow_vitality.data_dump.parse import DumpXmlError, iter_rows
from stackoverflow_vitality.data_dump.publish import (
    QualityLog,
    ShardRecord,
    ShardWriter,
    link_confirmed,
    write_jsonl,
)
from stackoverflow_vitality.data_dump.quality import RowContext, evaluate_row
from stackoverflow_vitality.data_dump.source import (
    DumpSourceError,
    SourceFacts,
    SourceMeasurement,
    hash_and_scan,
    hash_only,
    inspect_source,
    open_checked,
    relative_source,
)
from stackoverflow_vitality.storage.files import (
    ImmutableConflict,
    canonical,
    exclusive_lock,
    publish,
)

logger = logging.getLogger("stackoverflow_vitality.data_dump")
COUNTERS = (
    "rows_read",
    "posts_emitted",
    "questions_emitted",
    "answers_emitted",
    "posts_ignored_filter",
    "posts_skipped_type",
    "rejected_records",
    "duplicates_identical",
    "duplicates_divergent",
    "missing_parents",
    "owner_absent",
    "license_absent",
    "xml_failures",
    "unexpected_elements",
    "source_unstable",
    "limit_records",
    "limit_bytes",
    "limit_disk",
    "xml_forbidden",
    "api_conflicts",
)
_COUNTER_FOR = {
    "DD-001": "xml_failures",
    "DD-002": "unexpected_elements",
    "DD-003": "rejected_records",
    "DD-004": "rejected_records",
    "DD-006": "rejected_records",
    "DD-009": "rejected_records",
    "DD-010": "rejected_records",
    "DD-011": "rejected_records",
    "DD-012": "rejected_records",
    "DD-013": "rejected_records",
    "DD-017": "duplicates_divergent",
    "DD-019": "source_unstable",
    "DD-020": "limit_records",
    "DD-021": "limit_bytes",
    "DD-022": "limit_disk",
    "DD-023": "xml_forbidden",
}


def load_selection(path: Path) -> tuple[int, ...]:
    if "\x00" in str(path):
        raise ValueError("question id file is invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("question id file is invalid") from error
    return load_question_ids(payload)


def ingest_data_dump(
    config: DumpIngestConfig,
    source: Path,
    question_ids: tuple[int, ...],
    *,
    run_id: str,
    dry_run: bool = False,
    now: Callable[[], datetime] | None = None,
    disk_free: Callable[[Path], int] | None = None,
    rss_bytes: Callable[[], int | None] | None = None,
    before_manifest: Callable[[], None] | None = None,
    after_first_checksum: Callable[[], None] | None = None,
    attributes: Callable[[Path], int | None] | None = None,
) -> dict[str, Any]:
    config.validate()
    clock = now or (lambda: datetime.now(UTC))
    relative = relative_source(source, config.input_root) if config.record_relative_path else None
    if dry_run:
        facts = inspect_source(source, max_file_bytes=config.max_file_bytes, attributes=attributes)
        return _dry_run(config, facts, question_ids)
    _guard_path_length(config.site)
    stream, facts = open_checked(
        source, max_file_bytes=config.max_file_bytes, attributes=attributes
    )
    bronze = Path(config.bronze_output)
    counts = {name: 0 for name in COUNTERS}
    lock = bronze / "data_dump" / f"site={config.site}" / ".ingest.lock"
    try:
        with exclusive_lock(lock):
            try:
                return _run(
                    config,
                    stream,
                    facts,
                    question_ids,
                    run_id=run_id,
                    relative=relative,
                    counts=counts,
                    clock=clock,
                    disk_free=disk_free or _disk_free,
                    rss_bytes=rss_bytes or _rss_bytes,
                    before_manifest=before_manifest,
                    after_first_checksum=after_first_checksum,
                )
            except DumpAbort as error:
                _note(counts, error.code)
                failed = _failure(config, run_id, question_ids, counts, relative, error.code)
                _store_run(bronze, run_id, failed)
                if error.code in {"DD-020", "DD-021", "DD-022"}:
                    _log("dump_limit", run_id=run_id, failure_code=error.code, site=config.site)
                _log("dump_failed", run_id=run_id, failure_code=error.code, site=config.site)
                return failed
            except ImmutableConflict:
                failed = _failure(config, run_id, question_ids, counts, relative, "conflict")
                _store_run(bronze, run_id, failed)
                _log("dump_conflict", run_id=run_id, site=config.site)
                return failed
    finally:
        stream.close()


def _run(
    config: DumpIngestConfig,
    stream: Any,
    facts: SourceFacts,
    question_ids: tuple[int, ...],
    *,
    run_id: str,
    relative: str | None,
    counts: dict[str, int],
    clock: Callable[[], datetime],
    disk_free: Callable[[Path], int],
    rss_bytes: Callable[[], int | None],
    before_manifest: Callable[[], None] | None,
    after_first_checksum: Callable[[], None] | None,
) -> dict[str, Any]:
    observed = clock()
    started = clock()
    cutoff = None if config.dump_cutoff is None else parse_dump_instant(config.dump_cutoff[:-1])
    if cutoff is not None and cutoff > observed:
        raise ValueError("dump_cutoff is after source inspection")
    bronze = Path(config.bronze_output)
    try:
        free = disk_free(bronze)
    except OSError as error:
        raise DumpAbort("DD-022") from error
    if free < config.min_free_bytes:
        raise DumpAbort("DD-022")
    if config.max_rss_bytes is not None:
        measured = rss_bytes()
        if measured is None or measured > config.max_rss_bytes:
            raise DumpAbort("DD-021")
    _log(
        "dump_start",
        run_id=run_id,
        site=config.site,
        source_file_name=facts.name,
        mode=MODE,
    )
    before_digest, before_size = hash_and_scan(stream)
    if before_size != facts.size:
        raise DumpAbort("DD-019")
    before = SourceMeasurement(facts=facts, sha256=before_digest, size=before_size)
    _log("dump_source_validated", run_id=run_id, source_file_name=facts.name, bytes=before.size)
    _log("dump_checksum", run_id=run_id, phase="before", sha256=before.sha256, bytes=before.size)
    if after_first_checksum is not None:
        after_first_checksum()
    selected = selection_sha256(question_ids)
    source_key = source_identity(config.site, before.sha256, before.size)
    identity = ingestion_identity(
        source=source_key,
        selection=selected,
        max_attribute_bytes=config.max_attribute_bytes,
        max_body_chars=config.max_body_chars,
        max_shard_records=config.max_shard_records,
        max_shard_bytes=config.max_shard_bytes,
        dump_publication_id=config.publication_id,
        dump_cutoff=config.dump_cutoff,
        dump_cutoff_status=config.cutoff_status,
    )
    staging = bronze / "data_dump" / f"site={config.site}" / ".staging" / run_id
    writer = ShardWriter(
        staging, config.max_shard_records, config.max_shard_bytes, config.max_output_bytes
    )
    quality = QualityLog(staging)
    questions_seen: set[int] = set()
    pending_parents: dict[int, int] = {}
    seen: dict[int, tuple[int, str]] = {}
    context = RowContext(
        site=config.site,
        ingestion_identity=identity,
        source_file_name=facts.name,
        source_sha256=before.sha256,
        source_file_size=before.size,
        dump_publication_id=config.publication_id,
        selected=frozenset(question_ids),
        max_attribute_bytes=config.max_attribute_bytes,
        max_body_chars=config.max_body_chars,
        observed_at=observed,
        cutoff=cutoff,
    )
    _consume(
        stream,
        config,
        context,
        counts,
        writer,
        quality,
        questions_seen,
        pending_parents,
        seen,
        run_id,
        rss_bytes,
    )
    try:
        after_digest, after_size = hash_only(stream)
    except OSError as error:
        raise DumpAbort("DD-019") from error
    after = SourceMeasurement(facts=facts, sha256=after_digest, size=after_size)
    _log("dump_checksum", run_id=run_id, phase="after", sha256=after.sha256, bytes=after.size)
    if after.sha256 != before.sha256 or after.size != before.size:
        raise DumpAbort("DD-019")
    for parent_id, repeats in sorted(pending_parents.items()):
        counts["missing_parents"] += repeats
        quality.add(
            {
                "schema_version": "bronze_stackexchange_data_dump_quality.v1",
                "code": "DD-008",
                "parent_id": parent_id,
                "field": "ParentId",
            }
        )
        _log("dump_rejected", run_id=run_id, code="DD-008", parent_id=parent_id)
    shards = writer.close()
    for shard in shards:
        _log(
            "dump_shard_prepared",
            run_id=run_id,
            name=shard.name,
            records=shard.records,
            bytes=shard.size_bytes,
        )
        _log("dump_shard_confirmed", run_id=run_id, name=shard.name, sha256=shard.sha256)
    quarantine_path = quality.finish()
    quarantine_sha = _sha_file(quarantine_path)
    status = _status(counts)
    _log(
        "dump_rows_read",
        run_id=run_id,
        rows_read=counts["rows_read"],
        posts_emitted=counts["posts_emitted"],
    )
    _log(
        "dump_questions_selected",
        run_id=run_id,
        questions_emitted=counts["questions_emitted"],
    )
    _log("dump_answers_selected", run_id=run_id, answers_emitted=counts["answers_emitted"])
    _log("dump_ignored", run_id=run_id, posts_ignored_filter=counts["posts_ignored_filter"])
    if quarantine_path is not None:
        _log("dump_quarantine", run_id=run_id, records=quality.count)
    generation = bronze / "data_dump" / f"site={config.site}" / f"g={identity}"
    manifest_path = bronze / "data_dump" / "manifests" / f"{identity}.json"
    existing = _read_manifest(manifest_path)
    public_shards = [shard.public() for shard in shards]
    if existing is not None:
        matches = _same_generation(existing, public_shards, quarantine_sha) and _disk_matches(
            config, identity, shards, quarantine_sha
        )
        if matches:
            shutil.rmtree(staging, ignore_errors=True)
            _log("dump_replay", run_id=run_id, ingestion_identity=identity)
            replayed = dict(existing)
            replayed["replay"] = True
            return replayed
        raise ImmutableConflict("existing generation differs")
    prepared = _document(
        config,
        before,
        after,
        run_id,
        question_ids,
        counts,
        public_shards,
        relative,
        status=status,
        failure_code=None,
        published_at=None,
        started_at=stamp(started),
        observed_at=stamp(observed),
        source_key=source_key,
        identity=identity,
        selected=selected,
        quarantine_sha256=quarantine_sha,
    )
    write_jsonl(staging, "prepared.jsonl", [prepared])
    _reject_divergent_shards(generation, shards)
    for shard in shards:
        link_confirmed(shard.path, generation / shard.name)
    if quarantine_path is not None:
        link_confirmed(
            quarantine_path,
            Path(config.quarantine_output) / "data_dump" / f"g={identity}" / "items.jsonl",
        )
    if before_manifest is not None:
        before_manifest()
    published_at = stamp(clock())
    document = _document(
        config,
        before,
        after,
        run_id,
        question_ids,
        counts,
        public_shards,
        relative,
        status=status,
        failure_code=None,
        published_at=published_at,
        started_at=stamp(started),
        observed_at=stamp(observed),
        source_key=source_key,
        identity=identity,
        selected=selected,
        quarantine_sha256=quarantine_sha,
    )
    publish(manifest_path, canonical(document))
    shutil.rmtree(staging, ignore_errors=True)
    _log("dump_generation_published", run_id=run_id, ingestion_identity=identity, status=status)
    _log("dump_finished", run_id=run_id, status=status, ingestion_identity=identity)
    return document


def _consume(
    stream: Any,
    config: DumpIngestConfig,
    context: RowContext,
    counts: dict[str, int],
    writer: ShardWriter,
    quality: QualityLog,
    questions_seen: set[int],
    pending_parents: dict[int, int],
    seen: dict[int, tuple[int, str]],
    run_id: str,
    rss_bytes: Callable[[], int | None],
) -> None:
    try:
        rows = iter_rows(stream)
        for row_number, attributes in rows:
            counts["rows_read"] = row_number
            if row_number % config.progress_every == 0:
                _log("dump_progress", run_id=run_id, rows_read=row_number)
                if config.max_rss_bytes is not None:
                    measured = rss_bytes()
                    if measured is None or measured > config.max_rss_bytes:
                        raise DumpAbort("DD-021")
            outcome = evaluate_row(row_number, attributes, context)
            if (
                outcome.post_type_id == 1
                and outcome.post_id is not None
                and outcome.post_id in context.selected
            ):
                questions_seen.add(outcome.post_id)
                pending_parents.pop(outcome.post_id, None)
            if outcome.action == "abort":
                raise DumpAbort(outcome.code)
            if outcome.action == "skip":
                _skip(counts, outcome.code)
                continue
            if outcome.action == "quarantine":
                _reject(counts, quality, outcome.code, row_number, outcome)
                _log(
                    "dump_rejected",
                    run_id=run_id,
                    code=outcome.code,
                    source_row_number=row_number,
                    field=outcome.field,
                )
                continue
            if outcome.post_id is None or outcome.fingerprint is None or outcome.record is None:
                raise DumpAbort("DD-001")
            previous = seen.get(outcome.post_id)
            if previous is not None:
                if previous != (outcome.post_type_id, outcome.fingerprint):
                    _log("dump_rejected", run_id=run_id, code="DD-017", post_id=outcome.post_id)
                    raise DumpAbort("DD-017")
                counts["duplicates_identical"] += 1
                _log("dump_rejected", run_id=run_id, code="DD-016", post_id=outcome.post_id)
                continue
            if counts["posts_emitted"] >= config.max_records:
                raise DumpAbort("DD-020")
            writer.add(outcome.record, outcome.post_id)
            seen[outcome.post_id] = (outcome.post_type_id or 0, outcome.fingerprint)
            counts["posts_emitted"] += 1
            if outcome.post_type_id == 1:
                counts["questions_emitted"] += 1
            else:
                counts["answers_emitted"] += 1
                if outcome.parent_id is not None and outcome.parent_id not in questions_seen:
                    pending_parents[outcome.parent_id] = (
                        pending_parents.get(outcome.parent_id, 0) + 1
                    )
            for code in outcome.warnings:
                if code == "DD-014":
                    counts["owner_absent"] += 1
                elif code == "DD-015":
                    counts["license_absent"] += 1
                _log("dump_rejected", run_id=run_id, code=code, post_id=outcome.post_id)
    except DumpXmlError as error:
        raise DumpAbort(error.code) from error
    except DumpSourceError as error:
        raise DumpAbort("DD-019") from error


def _dry_run(
    config: DumpIngestConfig, facts: SourceFacts, question_ids: tuple[int, ...]
) -> dict[str, Any]:
    return {
        "status": "dry_run",
        "source_identity_status": "pending_source_checksum",
        "ingestion_identity_status": "pending_source_checksum",
        "site": config.site,
        "mode": MODE,
        "selection_count": len(question_ids),
        "source_file_name": facts.name,
        "source_file_size": facts.size,
        "dump_publication_id": config.publication_id,
        "dump_cutoff": config.dump_cutoff,
        "dump_cutoff_status": config.cutoff_status,
        "checksum_on_execute": "full_before_and_after",
    }


def _failure(
    config: DumpIngestConfig,
    run_id: str,
    question_ids: tuple[int, ...],
    counts: dict[str, int],
    relative: str | None,
    failure_code: str,
) -> dict[str, Any]:
    return _document(
        config,
        None,
        None,
        run_id,
        question_ids,
        counts,
        [],
        relative,
        status="failed",
        failure_code=failure_code,
        published_at=None,
        started_at=None,
        observed_at=None,
    )


def _document(
    config: DumpIngestConfig,
    before: SourceMeasurement | None,
    after: SourceMeasurement | None,
    run_id: str,
    question_ids: tuple[int, ...],
    counts: dict[str, int],
    shards: list[dict[str, Any]],
    relative: str | None,
    *,
    status: str,
    failure_code: str | None,
    published_at: str | None,
    started_at: str | None,
    observed_at: str | None,
    source_key: str | None = None,
    identity: str | None = None,
    selected: str | None = None,
    quarantine_sha256: str | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": MANIFEST_VERSION,
        "source_identity": source_key,
        "ingestion_identity": identity,
        "run_id": run_id,
        "site": config.site,
        "dump_publication_id": config.publication_id,
        "dump_cutoff": config.dump_cutoff,
        "dump_cutoff_status": config.cutoff_status,
        "source_file_name": None if before is None else before.facts.name,
        "source_sha256": None if before is None else before.sha256,
        "source_sha256_after": None if after is None else after.sha256,
        "source_file_size": None if before is None else before.size,
        "source_file_size_after": None if after is None else after.size,
        "source_file_observed_at": observed_at,
        "run_started_at": started_at,
        "published_at": published_at,
        "transformation_run_id": None,
        "parser_version": "1.0.0",
        "contract_version": "bronze_stackexchange_data_dump_posts.v1",
        "project_revision": config.project_revision,
        "status": status,
        "readable": _access(status)[0],
        "consumable": _access(status)[1],
        "failure_code": failure_code,
        "mode": MODE,
        "selection_sha256": selected if selected is not None else selection_sha256(question_ids),
        "counts": dict(counts),
        "shards": shards,
        "limits": {
            "max_attribute_bytes": config.max_attribute_bytes,
            "max_body_chars": config.max_body_chars,
            "max_shard_records": config.max_shard_records,
            "max_shard_bytes": config.max_shard_bytes,
            "max_records": config.max_records,
            "max_output_bytes": config.max_output_bytes,
            "max_file_bytes": config.max_file_bytes,
        },
        "api_comparison": "skipped",
        "parameters": {
            "selection_count": len(question_ids),
            "record_relative_path": config.record_relative_path,
            "source_relative_path": relative,
            "progress_every": config.progress_every,
            "min_free_bytes": config.min_free_bytes,
        },
        "quarantine_sha256": quarantine_sha256,
    }
    return document


def _access(status: str) -> tuple[bool, bool]:
    if status in {"completed", "empty"}:
        return True, True
    if status == "partial":
        return True, False
    return False, False


def _status(counts: dict[str, int]) -> str:
    if counts["rejected_records"] or counts["missing_parents"]:
        return "partial"
    if counts["posts_emitted"] == 0:
        return "empty"
    return "completed"


def _skip(counts: dict[str, int], code: str) -> None:
    if code == "DD-005":
        counts["posts_skipped_type"] += 1
    elif code == "DD-007":
        counts["posts_ignored_filter"] += 1


def _reject(
    counts: dict[str, int],
    quality: QualityLog,
    code: str,
    row_number: int,
    outcome: Any,
) -> None:
    _note(counts, code)
    item: dict[str, Any] = {
        "schema_version": "bronze_stackexchange_data_dump_quality.v1",
        "code": code,
        "source_row_number": row_number,
        "post_id": outcome.post_id,
        "field": outcome.field,
    }
    if code == "DD-012" and outcome.detail is not None:
        item["length"] = int(outcome.detail)
    quality.add(item)


def _note(counts: dict[str, int], code: str) -> None:
    name = _COUNTER_FOR.get(code)
    if name is not None:
        counts[name] += 1


def _same_generation(
    existing: dict[str, Any], shards: list[dict[str, Any]], quarantine_sha256: str | None
) -> bool:
    if existing.get("status") not in {"completed", "empty", "partial"}:
        return False
    return (
        _signature(existing.get("shards", [])) == _signature(shards)
        and existing.get("quarantine_sha256") == quarantine_sha256
    )


def _signature(shards: list[dict[str, Any]]) -> list[tuple[str, str, int, int]]:
    return [
        (str(item["name"]), str(item["sha256"]), int(item["records"]), int(item["bytes"]))
        for item in shards
    ]


def _disk_matches(
    config: DumpIngestConfig,
    identity: str,
    shards: Sequence[ShardRecord],
    quarantine_sha256: str | None,
) -> bool:
    import hashlib

    generation = Path(config.bronze_output) / "data_dump" / f"site={config.site}" / f"g={identity}"
    for shard in shards:
        path = generation / shard.name
        if not path.is_file():
            return False
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != shard.sha256 or len(payload) != shard.size_bytes:
            return False
    quarantine = Path(config.quarantine_output) / "data_dump" / f"g={identity}" / "items.jsonl"
    if quarantine_sha256 is None:
        return not quarantine.exists()
    if not quarantine.is_file():
        return False
    return hashlib.sha256(quarantine.read_bytes()).hexdigest() == quarantine_sha256


def _reject_divergent_shards(generation: Path, shards: Sequence[ShardRecord]) -> None:
    if not generation.exists():
        return
    expected = {shard.name for shard in shards}
    present = {path.name for path in generation.glob("shard=*.jsonl")}
    if present - expected:
        raise ImmutableConflict("existing generation differs")


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ImmutableConflict("existing generation differs")
    return payload


def _store_run(bronze: Path, run_id: str, document: dict[str, Any]) -> None:
    publish(bronze / "data_dump" / "manifests" / "runs" / f"{run_id}.json", canonical(document))


def _sha_file(path: Path | None) -> str | None:
    if path is None:
        return None
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _guard_path_length(site: str) -> None:
    sample = f"data_dump/site={site}/g={'a' * 64}/shard=000001.jsonl"
    if len(sample) > 180:
        raise ValueError("generation path exceeds the local limit")


def _disk_free(path: Path) -> int:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise DumpAbort("DD-022")
        current = parent
    try:
        return int(shutil.disk_usage(current).free)
    except OSError as error:
        raise DumpAbort("DD-022") from error


def _rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    getrusage = getattr(resource, "getrusage", None)
    rusage_self = getattr(resource, "RUSAGE_SELF", None)
    if getrusage is None or rusage_self is None:
        return None
    try:
        usage = getrusage(rusage_self)
        return int(usage.ru_maxrss)
    except (AttributeError, OSError, ValueError):
        return None


def _log(event: str, **fields: object) -> None:
    payload: dict[str, object] = {"event": event}
    payload.update(fields)
    logger.info(json.dumps(payload, sort_keys=True, default=str))
