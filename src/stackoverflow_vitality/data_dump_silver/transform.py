"""Transform one consumable Data Dump Bronze generation into snapshot Silver."""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stackoverflow_vitality.data_dump.dates import parse_dump_instant, stamp
from stackoverflow_vitality.data_dump.identity import (
    CONTRACT_VERSION,
    MANIFEST_VERSION,
    SOURCE_KIND,
)
from stackoverflow_vitality.data_dump.publish import link_confirmed, write_private
from stackoverflow_vitality.data_dump_silver.derive import derive_body, derive_tags
from stackoverflow_vitality.data_dump_silver.identity import (
    ANSWER_CONTENT_HASH_FIELDS,
    ANSWER_CONTENT_SCHEMA,
    ANSWER_OBSERVATIONS_SCHEMA,
    BODY_RULES,
    MANIFEST_SCHEMA,
    QUALITY_SCHEMA,
    QUESTION_CONTENT_HASH_FIELDS,
    QUESTION_CONTENT_SCHEMA,
    QUESTION_OBSERVATIONS_SCHEMA,
    TAG_RULES,
    TRANSFORMER_VERSION,
    content_hash,
    digest,
    silver_generation_id,
)
from stackoverflow_vitality.storage.files import (
    ImmutableConflict,
    IntegrityError,
    LocalLockConflict,
    canonical,
    exclusive_lock,
    publish,
)

logger = logging.getLogger(__name__)

_SHARD_NAME = re.compile(r"shard=\d{6}\.jsonl")
_HEX_ID = re.compile(r"[0-9a-f]{64}")
_SITE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_REJECTIONS = {"DS-007", "DS-008", "DS-010", "DS-011", "DS-012"}
_DATASETS = (
    ("question_content.jsonl", QUESTION_CONTENT_SCHEMA, "question_content"),
    ("question_observations.jsonl", QUESTION_OBSERVATIONS_SCHEMA, "question_observations"),
    ("answer_content.jsonl", ANSWER_CONTENT_SCHEMA, "answer_content"),
    ("answer_observations.jsonl", ANSWER_OBSERVATIONS_SCHEMA, "answer_observations"),
)
_SIGNATURE = (
    "post_type_id",
    "parent_id",
    "accepted_answer_id",
    "source_created_at",
    "score",
    "body",
    "title",
    "tags_raw",
    "owner_user_id",
    "content_license",
    "last_edit_at",
    "last_activity_at",
    "closed_at",
    "view_count",
    "answer_count",
    "comment_count",
)


class GenerationAbort(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RowReject(Exception):
    def __init__(self, code: str, field: str) -> None:
        self.code = code
        self.field = field
        super().__init__(code)


def transform_data_dump(
    bronze_manifest: Path,
    output_dir: Path,
    *,
    transformation_run_id: str,
    max_body_chars: int = 500_000,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Publish one snapshot Silver generation, or refuse before any Silver write."""

    if max_body_chars < 1:
        raise ValueError("max_body_chars must be positive")
    ingestion: str | None = None
    generation: str | None = None
    try:
        manifest = _load_manifest(bronze_manifest)
        _require_consumable(manifest)
        ingestion = str(manifest["ingestion_identity"])
        generation = silver_generation_id(
            ingestion_identity=ingestion, max_body_chars=max_body_chars
        )
        rows = _read_rows(bronze_manifest, manifest)
        built = _build(manifest, rows, max_body_chars=max_body_chars)
        if dry_run:
            if _destination_conflict(output_dir, generation, built):
                return _failed("DS-015", ingestion=ingestion, generation=generation, dry_run=True)
            return _preview(manifest, built, generation)
        try:
            with exclusive_lock(output_dir / ".transform.lock"):
                return _publish(
                    output_dir,
                    manifest,
                    built,
                    generation=generation,
                    transformation_run_id=transformation_run_id,
                    max_body_chars=max_body_chars,
                )
        except LocalLockConflict:
            return _failed("DS-017", ingestion=ingestion, generation=generation, dry_run=False)
    except GenerationAbort as error:
        return _failed(error.code, ingestion=ingestion, generation=generation, dry_run=dry_run)


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise GenerationAbort("DS-001")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GenerationAbort("DS-001") from None
    if not isinstance(parsed, dict) or parsed.get("published_at") in (None, ""):
        raise GenerationAbort("DS-001")
    if parsed.get("schema_version") != MANIFEST_VERSION:
        raise GenerationAbort("DS-003")
    return parsed


def _require_consumable(manifest: dict[str, Any]) -> None:
    if (
        manifest.get("status") not in {"completed", "empty"}
        or manifest.get("consumable") is not True
    ):
        raise GenerationAbort("DS-002")
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise GenerationAbort("DS-003")
    identity = manifest.get("ingestion_identity")
    site = manifest.get("site")
    source = manifest.get("source_identity")
    publication = manifest.get("dump_publication_id")
    if (
        not isinstance(identity, str)
        or _HEX_ID.fullmatch(identity) is None
        or not isinstance(source, str)
        or _HEX_ID.fullmatch(source) is None
        or not isinstance(site, str)
        or _SITE.fullmatch(site) is None
        or not isinstance(publication, str)
        or not publication
    ):
        raise GenerationAbort("DS-006")
    cutoff_status = manifest.get("dump_cutoff_status")
    if cutoff_status not in {"operator_declared", "unknown"}:
        raise GenerationAbort("DS-006")
    cutoff = manifest.get("dump_cutoff")
    observed = manifest.get("source_file_observed_at")
    if cutoff is not None and (not isinstance(cutoff, str) or _instant(cutoff) is None):
        raise GenerationAbort("DS-010")
    if not isinstance(observed, str) or _instant(observed) is None:
        raise GenerationAbort("DS-010")


def _read_rows(manifest_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        raise GenerationAbort("DS-004")
    if manifest.get("status") == "empty" and shards:
        raise GenerationAbort("DS-004")
    root = (
        manifest_path.parent.parent
        / f"site={manifest['site']}"
        / f"g={manifest['ingestion_identity']}"
    )
    rows: list[dict[str, Any]] = []
    for shard in shards:
        rows.extend(_read_shard(root, shard, manifest))
    return rows


def _read_shard(root: Path, shard: object, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(shard, dict):
        raise GenerationAbort("DS-004")
    name = shard.get("name")
    if not isinstance(name, str) or _SHARD_NAME.fullmatch(name) is None:
        raise GenerationAbort("DS-004")
    path = root / name
    if path.parent != root or not path.is_file():
        raise GenerationAbort("DS-004")
    try:
        payload = path.read_bytes()
    except OSError:
        raise GenerationAbort("DS-004") from None
    if digest(payload) != shard.get("sha256") or len(payload) != shard.get("bytes"):
        raise GenerationAbort("DS-004")
    lines = payload.splitlines()
    if len(lines) != shard.get("records") or (payload and not payload.endswith(b"\n")):
        raise GenerationAbort("DS-004")
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            raise GenerationAbort("DS-005") from None
        if not isinstance(parsed, dict):
            raise GenerationAbort("DS-005")
        if parsed.get("schema_version") != CONTRACT_VERSION:
            raise GenerationAbort("DS-003")
        if (
            parsed.get("source_kind") != SOURCE_KIND
            or parsed.get("site") != manifest["site"]
            or parsed.get("ingestion_identity") != manifest["ingestion_identity"]
        ):
            raise GenerationAbort("DS-006")
        rows.append(parsed)
    return rows


def _build(
    manifest: dict[str, Any], rows: list[dict[str, Any]], *, max_body_chars: int
) -> dict[str, Any]:
    questions: dict[int, dict[str, Any]] = {}
    answers: dict[int, dict[str, Any]] = {}
    seen: dict[int, bytes] = {}
    quality: list[dict[str, Any]] = []
    duplicates = 0
    for row in rows:
        try:
            post_id = _positive(row.get("post_id"), "post_id")
            signature = canonical({key: row.get(key) for key in _SIGNATURE})
            previous = seen.get(post_id)
            if previous is not None:
                if previous != signature:
                    raise GenerationAbort("DS-009")
                duplicates += 1
                continue
            seen[post_id] = signature
            _store_row(manifest, row, post_id, questions, answers, max_body_chars)
        except RowReject as error:
            quality.append(_quality(error.code, row.get("post_id"), error.field))
    _acceptance(questions, answers, quality)
    question_content = [_question_content(item) for item in questions.values()]
    question_observations = [_question_observation(item) for item in questions.values()]
    answer_content = [_answer_content(item) for item in answers.values()]
    answer_observations = [_answer_observation(item) for item in answers.values()]
    question_content.sort(
        key=lambda item: (item["site"], item["question_id"], item["content_hash"])
    )
    question_observations.sort(
        key=lambda item: (item["site"], item["question_id"], item["ingestion_identity"])
    )
    answer_content.sort(key=lambda item: (item["site"], item["answer_id"], item["content_hash"]))
    answer_observations.sort(
        key=lambda item: (item["site"], item["answer_id"], item["ingestion_identity"])
    )
    quality.sort(key=lambda item: (item["code"], item.get("post_id") or 0, item["field"]))
    rejections = sum(1 for item in quality if item["code"] in _REJECTIONS)
    if rejections:
        status = "partial"
    elif not question_content and not answer_content:
        status = "empty"
    else:
        status = "completed"
    readable, consumable = _access(status)
    return {
        "question_content": question_content,
        "question_observations": question_observations,
        "answer_content": answer_content,
        "answer_observations": answer_observations,
        "quality": quality,
        "status": status,
        "readable": readable,
        "consumable": consumable,
        "identical_duplicates": duplicates,
        "rejected": rejections,
        "warnings": len(quality) - rejections,
    }


def _store_row(
    manifest: dict[str, Any],
    row: dict[str, Any],
    post_id: int,
    questions: dict[int, dict[str, Any]],
    answers: dict[int, dict[str, Any]],
    max_body_chars: int,
) -> None:
    post_type = row.get("post_type_id")
    if type(post_type) is not int or post_type not in {1, 2}:
        raise RowReject("DS-007", "post_type_id")
    source_row = row.get("source_row_number")
    if type(source_row) is not int or source_row < 1:
        raise RowReject("DS-007", "source_row_number")
    body = _text(row.get("body"), "body")
    if body is not None and len(body) > max_body_chars:
        raise RowReject("DS-012", "body")
    created = _timestamp(row.get("source_created_at"), "source_created_at")
    if _after_cutoff(created, manifest.get("dump_cutoff"), manifest.get("dump_cutoff_status")):
        raise RowReject("DS-011", "source_created_at")
    common = {
        "site": manifest["site"],
        "source_kind": SOURCE_KIND,
        "body_html": body,
        "owner_user_id": _optional_id(row.get("owner_user_id"), "owner_user_id"),
        "content_license": _text(row.get("content_license"), "content_license"),
        "source_created_at": created,
        "last_edit_at": _timestamp(row.get("last_edit_at"), "last_edit_at"),
        "source_row_number": source_row,
        "score": _score(row.get("score")),
        "comment_count": _count(row.get("comment_count"), "comment_count"),
        "last_activity_at": _timestamp(row.get("last_activity_at"), "last_activity_at"),
        "lineage": _lineage(manifest),
    }
    text, blocks = derive_body(body)
    common["body_text"] = text
    common["code_blocks"] = blocks
    if post_type == 1:
        common["question_id"] = post_id
        common["title"] = _text(row.get("title"), "title")
        common["tags_raw"] = _text(row.get("tags_raw"), "tags_raw")
        common["tags"] = derive_tags(common["tags_raw"])
        common["closed_at"] = _timestamp(row.get("closed_at"), "closed_at")
        common["accepted_answer_id"] = _optional_id(
            row.get("accepted_answer_id"), "accepted_answer_id"
        )
        common["view_count"] = _count(row.get("view_count"), "view_count")
        common["answer_count"] = _count(row.get("answer_count"), "answer_count")
        questions[post_id] = common
        return
    parent = row.get("parent_id")
    if parent is None:
        raise RowReject("DS-008", "parent_id")
    common["answer_id"] = post_id
    common["question_id"] = _positive(parent, "parent_id")
    answers[post_id] = common


def _acceptance(
    questions: dict[int, dict[str, Any]],
    answers: dict[int, dict[str, Any]],
    quality: list[dict[str, Any]],
) -> None:
    available: dict[int, set[int]] = {}
    for answer_id, item in answers.items():
        available.setdefault(int(item["question_id"]), set()).add(answer_id)
    for question_id, item in questions.items():
        accepted = item["accepted_answer_id"]
        if type(accepted) is int and accepted not in available.get(question_id, set()):
            quality.append(_quality("DS-014", question_id, "accepted_answer_id"))
    for answer_id, item in answers.items():
        question = questions.get(int(item["question_id"]))
        if question is None:
            item["is_accepted_in_snapshot"] = None
            quality.append(_quality("DS-013", answer_id, "question_id"))
            continue
        accepted = question["accepted_answer_id"]
        if type(accepted) is not int:
            item["is_accepted_in_snapshot"] = False
            continue
        item["is_accepted_in_snapshot"] = answer_id == accepted


def _question_content(item: dict[str, Any]) -> dict[str, Any]:
    fields = {key: item[key] for key in QUESTION_CONTENT_HASH_FIELDS}
    return {
        "schema_version": QUESTION_CONTENT_SCHEMA,
        "source_kind": SOURCE_KIND,
        "content_hash": content_hash(fields),
        **fields,
    }


def _answer_content(item: dict[str, Any]) -> dict[str, Any]:
    fields = {key: item[key] for key in ANSWER_CONTENT_HASH_FIELDS}
    return {
        "schema_version": ANSWER_CONTENT_SCHEMA,
        "source_kind": SOURCE_KIND,
        "content_hash": content_hash(fields),
        **fields,
    }


def _question_observation(item: dict[str, Any]) -> dict[str, Any]:
    fields = {key: item[key] for key in QUESTION_CONTENT_HASH_FIELDS}
    return {
        "schema_version": QUESTION_OBSERVATIONS_SCHEMA,
        "source_kind": SOURCE_KIND,
        "site": item["site"],
        "question_id": item["question_id"],
        "content_hash": content_hash(fields),
        "score": item["score"],
        "view_count": item["view_count"],
        "answer_count": item["answer_count"],
        "comment_count": item["comment_count"],
        "accepted_answer_id": item["accepted_answer_id"],
        "last_activity_at": item["last_activity_at"],
        "source_row_number": item["source_row_number"],
        **item["lineage"],
    }


def _answer_observation(item: dict[str, Any]) -> dict[str, Any]:
    fields = {key: item[key] for key in ANSWER_CONTENT_HASH_FIELDS}
    return {
        "schema_version": ANSWER_OBSERVATIONS_SCHEMA,
        "source_kind": SOURCE_KIND,
        "site": item["site"],
        "answer_id": item["answer_id"],
        "question_id": item["question_id"],
        "content_hash": content_hash(fields),
        "score": item["score"],
        "comment_count": item["comment_count"],
        "last_activity_at": item["last_activity_at"],
        "is_accepted_in_snapshot": item["is_accepted_in_snapshot"],
        "source_row_number": item["source_row_number"],
        **item["lineage"],
    }


def _lineage(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "dump_publication_id": manifest["dump_publication_id"],
        "dump_cutoff": manifest["dump_cutoff"],
        "dump_cutoff_status": manifest["dump_cutoff_status"],
        "source_file_observed_at": manifest["source_file_observed_at"],
        "source_identity": manifest["source_identity"],
        "ingestion_identity": manifest["ingestion_identity"],
    }


def _publish(
    output_dir: Path,
    manifest: dict[str, Any],
    built: dict[str, Any],
    *,
    generation: str,
    transformation_run_id: str,
    max_body_chars: int,
) -> dict[str, Any]:
    datasets = _dataset_payloads(built)
    quarantine = _quarantine_payload(built["quality"])
    generation_dir = output_dir / f"g={generation}"
    manifest_path = output_dir / "manifests" / f"{generation}.json"
    quarantine_path = output_dir / "quarantine" / f"g={generation}" / "items.jsonl"
    existing = _existing_manifest(manifest_path)
    if existing is not None:
        if _same_generation(existing, datasets, quarantine, built, generation):
            if _files_match(generation_dir, datasets, quarantine_path, quarantine):
                replayed = dict(existing)
                replayed["replay"] = True
                replayed["dry_run"] = False
                return replayed
        _run_record(output_dir, transformation_run_id, "DS-015", generation)
        return _failed(
            "DS-015",
            ingestion=str(manifest["ingestion_identity"]),
            generation=generation,
            dry_run=False,
        )
    if _divergent_targets(generation_dir, datasets, quarantine_path, quarantine):
        _run_record(output_dir, transformation_run_id, "DS-015", generation)
        return _failed(
            "DS-015",
            ingestion=str(manifest["ingestion_identity"]),
            generation=generation,
            dry_run=False,
        )
    document = _document(
        manifest,
        built,
        generation=generation,
        transformation_run_id=transformation_run_id,
        max_body_chars=max_body_chars,
        datasets=datasets,
        quarantine=quarantine,
    )
    staging = output_dir / ".staging" / transformation_run_id
    try:
        if staging.exists():
            shutil.rmtree(staging)
        for item in datasets:
            staged = staging / item["name"]
            write_private(staged, item["payload"])
            link_confirmed(staged, generation_dir / item["name"])
        if quarantine is not None:
            staged_quality = staging / "items.jsonl"
            write_private(staged_quality, quarantine)
            link_confirmed(staged_quality, quarantine_path)
        payload = canonical(document)
        publish(manifest_path, payload)
        if manifest_path.read_bytes() != payload:
            raise IntegrityError("terminal manifest differs from the confirmed bytes")
    except ImmutableConflict:
        _run_record(output_dir, transformation_run_id, "DS-015", generation)
        return _failed(
            "DS-015",
            ingestion=str(manifest["ingestion_identity"]),
            generation=generation,
            dry_run=False,
        )
    except (OSError, IntegrityError):
        _run_record(output_dir, transformation_run_id, "DS-016", generation)
        return _failed(
            "DS-016",
            ingestion=str(manifest["ingestion_identity"]),
            generation=generation,
            dry_run=False,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    logger.info("data dump silver %s %s", generation, built["status"])
    returned = dict(document)
    returned["replay"] = False
    returned["dry_run"] = False
    return returned


def _dataset_payloads(built: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for name, schema, key in _DATASETS:
        payload = b"".join(canonical(row) + b"\n" for row in built[key])
        payloads.append(
            {
                "name": name,
                "schema_version": schema,
                "sha256": digest(payload),
                "records": len(built[key]),
                "bytes": len(payload),
                "payload": payload,
            }
        )
    return payloads


def _quarantine_payload(quality: list[dict[str, Any]]) -> bytes | None:
    if not quality:
        return None
    return b"".join(canonical(item) + b"\n" for item in quality)


def _document(
    manifest: dict[str, Any],
    built: dict[str, Any],
    *,
    generation: str,
    transformation_run_id: str,
    max_body_chars: int,
    datasets: list[dict[str, Any]],
    quarantine: bytes | None,
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA,
        "silver_generation_id": generation,
        "ingestion_identity": manifest["ingestion_identity"],
        "transformation_run_id": transformation_run_id,
        "status": built["status"],
        "readable": built["readable"],
        "consumable": built["consumable"],
        "published_at": stamp(datetime.now(UTC)),
        "transformer_version": TRANSFORMER_VERSION,
        "body_rules": BODY_RULES,
        "tag_rules": TAG_RULES,
        "max_body_chars": max_body_chars,
        "contracts": {
            "question_content": QUESTION_CONTENT_SCHEMA,
            "question_observations": QUESTION_OBSERVATIONS_SCHEMA,
            "answer_content": ANSWER_CONTENT_SCHEMA,
            "answer_observations": ANSWER_OBSERVATIONS_SCHEMA,
        },
        "datasets": [
            {key: item[key] for key in ("name", "schema_version", "sha256", "records", "bytes")}
            for item in datasets
        ],
        "quarantine_sha256": None if quarantine is None else digest(quarantine),
        "quarantine_records": 0 if quarantine is None else built["rejected"] + built["warnings"],
        "counts": {
            "question_content": len(built["question_content"]),
            "question_observations": len(built["question_observations"]),
            "answer_content": len(built["answer_content"]),
            "answer_observations": len(built["answer_observations"]),
            "rejected": built["rejected"],
            "warnings": built["warnings"],
            "identical_duplicates": built["identical_duplicates"],
        },
        "failure_code": None,
    }


def _existing_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GenerationAbort("DS-015") from None
    if not isinstance(parsed, dict):
        raise GenerationAbort("DS-015")
    return parsed


def _same_generation(
    existing: dict[str, Any],
    datasets: list[dict[str, Any]],
    quarantine: bytes | None,
    built: dict[str, Any],
    generation: str,
) -> bool:
    stored = existing.get("datasets")
    if not isinstance(stored, list) or len(stored) != len(datasets):
        return False
    for left, right in zip(stored, datasets, strict=True):
        if not isinstance(left, dict):
            return False
        if (
            left.get("name") != right["name"]
            or left.get("sha256") != right["sha256"]
            or left.get("records") != right["records"]
            or left.get("bytes") != right["bytes"]
        ):
            return False
    expected_quarantine = None if quarantine is None else digest(quarantine)
    return (
        existing.get("silver_generation_id") == generation
        and existing.get("status") == built["status"]
        and existing.get("readable") is built["readable"]
        and existing.get("consumable") is built["consumable"]
        and existing.get("quarantine_sha256") == expected_quarantine
    )


def _files_match(
    directory: Path,
    datasets: list[dict[str, Any]],
    quarantine_path: Path,
    quarantine: bytes | None,
) -> bool:
    for item in datasets:
        path = directory / str(item["name"])
        try:
            if not path.is_file() or path.read_bytes() != item["payload"]:
                return False
        except OSError:
            return False
    try:
        if quarantine is None:
            return not quarantine_path.exists()
        return quarantine_path.is_file() and quarantine_path.read_bytes() == quarantine
    except OSError:
        return False


def _divergent_targets(
    directory: Path,
    datasets: list[dict[str, Any]],
    quarantine_path: Path,
    quarantine: bytes | None,
) -> bool:
    for item in datasets:
        path = directory / str(item["name"])
        if not path.exists():
            continue
        try:
            if path.read_bytes() != item["payload"]:
                return True
        except OSError:
            return True
    if not quarantine_path.exists():
        return False
    try:
        return quarantine is None or quarantine_path.read_bytes() != quarantine
    except OSError:
        return True


def _destination_conflict(output_dir: Path, generation: str, built: dict[str, Any]) -> bool:
    datasets = _dataset_payloads(built)
    quarantine = _quarantine_payload(built["quality"])
    generation_dir = output_dir / f"g={generation}"
    manifest_path = output_dir / "manifests" / f"{generation}.json"
    quarantine_path = output_dir / "quarantine" / f"g={generation}" / "items.jsonl"
    existing = _existing_manifest(manifest_path)
    if existing is None:
        return _divergent_targets(generation_dir, datasets, quarantine_path, quarantine)
    if _same_generation(existing, datasets, quarantine, built, generation) and _files_match(
        generation_dir, datasets, quarantine_path, quarantine
    ):
        return False
    return True


def _preview(manifest: dict[str, Any], built: dict[str, Any], generation: str) -> dict[str, Any]:
    planned = str(built["status"])
    status = "dry_run" if planned in {"completed", "empty"} else planned
    return {
        "schema_version": MANIFEST_SCHEMA,
        "silver_generation_id": generation,
        "ingestion_identity": manifest["ingestion_identity"],
        "status": status,
        "planned_status": planned,
        "readable": False,
        "consumable": False,
        "dry_run": True,
        "replay": False,
        "failure_code": None,
        "counts": {
            "question_content": len(built["question_content"]),
            "question_observations": len(built["question_observations"]),
            "answer_content": len(built["answer_content"]),
            "answer_observations": len(built["answer_observations"]),
            "rejected": built["rejected"],
            "warnings": built["warnings"],
        },
        "destinations": [
            f"g={generation}/question_content.jsonl",
            f"g={generation}/question_observations.jsonl",
            f"g={generation}/answer_content.jsonl",
            f"g={generation}/answer_observations.jsonl",
            f"manifests/{generation}.json",
        ],
    }


def _failed(
    code: str, *, ingestion: str | None, generation: str | None, dry_run: bool
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA,
        "status": "failed",
        "readable": False,
        "consumable": False,
        "failure_code": code,
        "silver_generation_id": generation,
        "ingestion_identity": ingestion,
        "dry_run": dry_run,
        "replay": False,
    }


def _run_record(output_dir: Path, run_id: str, code: str, generation: str) -> None:
    document = {
        "schema_version": MANIFEST_SCHEMA,
        "transformation_run_id": run_id,
        "silver_generation_id": generation,
        "status": "failed",
        "readable": False,
        "consumable": False,
        "failure_code": code,
        "published_at": stamp(datetime.now(UTC)),
    }
    try:
        publish(output_dir / "manifests" / "runs" / f"{run_id}.json", canonical(document))
    except (OSError, ImmutableConflict, IntegrityError):
        logger.info("data dump silver run record was not published")


def _quality(code: str, post_id: object, field: str) -> dict[str, Any]:
    return {
        "schema_version": QUALITY_SCHEMA,
        "code": code,
        "severity": "warning" if code not in _REJECTIONS else "error",
        "post_id": post_id if type(post_id) is int else None,
        "field": field,
        "line_effect": "kept" if code not in _REJECTIONS else "dropped",
        "run_effect": "completed" if code not in _REJECTIONS else "partial",
    }


def _access(status: str) -> tuple[bool, bool]:
    if status == "completed":
        return True, True
    if status == "empty":
        return True, True
    if status == "partial":
        return True, False
    return False, False


def _instant(value: str) -> datetime | None:
    text = value[:-1] if value.endswith("Z") else value
    return parse_dump_instant(text)


def _timestamp(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _instant(value) is None:
        raise RowReject("DS-010", field)
    return value


def _after_cutoff(created: str | None, cutoff: object, status: object) -> bool:
    if status != "operator_declared" or not isinstance(cutoff, str) or created is None:
        return False
    left = _instant(created)
    right = _instant(cutoff)
    if left is None or right is None:
        return False
    return left > right


def _positive(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise RowReject("DS-007", field)
    return value


def _optional_id(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _positive(value, field)


def _count(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise RowReject("DS-007", field)
    return value


def _score(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise RowReject("DS-007", "score")
    return value


def _text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RowReject("DS-007", field)
    return value
