"""Order-independent answer history. Score and acceptance are not content."""

from __future__ import annotations

import hashlib
from typing import Any

from stackoverflow_vitality.answers.quality import (
    CATALOG_SCHEMA,
    CONTENT_FIELDS,
    CONTENT_SCHEMA,
    ENTITY_TYPE,
    METRIC_SCHEMA,
)
from stackoverflow_vitality.storage.files import canonical


def empty_state() -> dict[str, Any]:
    return {"catalog": {}, "observations": {}, "versions": []}


def content_hash(record: dict[str, Any]) -> str:
    payload = {field: record[field] for field in CONTENT_FIELDS}
    payload["schema_version"] = CONTENT_SCHEMA
    payload["entity_type"] = ENTITY_TYPE
    return hashlib.sha256(canonical(payload)).hexdigest()


def observation_id(record: dict[str, Any]) -> str:
    identity = {
        key: record[key]
        for key in (
            "site",
            "source_run_id",
            "source_batch_id",
            "source_page",
            "source_file_checksum",
            "source_item_index",
        )
    }
    return hashlib.sha256(canonical(identity)).hexdigest()


def observe(state: dict[str, Any], record: dict[str, Any]) -> bool:
    record_hash = content_hash(record)
    identity = observation_id(record)
    catalog = {
        "schema_version": CATALOG_SCHEMA,
        "entity_type": ENTITY_TYPE,
        "record_hash": record_hash,
        "checksum": record_hash,
        "site": record["site"],
        "answer_id": record["answer_id"],
        "question_id": record["question_id"],
        "creation_at": record["creation_at"],
        "last_edit_at": record["last_edit_at"],
        "owner_user_id": record["owner_user_id"],
        "body_html": record["body_html"],
        "body_text": record["body_text"],
        "code_blocks": list(record["code_blocks"]),
        "content_license": record["content_license"],
    }
    observation = {
        "schema_version": METRIC_SCHEMA,
        "site": record["site"],
        "answer_id": record["answer_id"],
        "question_id": record["question_id"],
        "collected_at": record["collected_at"],
        "last_activity_at": record["last_activity_at"],
        "score": record["score"],
        "is_accepted": record["is_accepted"],
        "record_hash": record_hash,
        "observation_id": identity,
        "quality": {"warnings": list(record["warnings"])},
        "lineage": {
            "source_run_id": record["source_run_id"],
            "source_batch_id": record["source_batch_id"],
            "source_page": record["source_page"],
            "source_file_checksum": record["source_file_checksum"],
            "source_item_index": record["source_item_index"],
        },
    }
    existing = state["observations"].get(identity)
    if existing is not None:
        if existing != observation:
            raise ValueError("observation identity conflict")
        return False
    previous = state["catalog"].get(record_hash)
    if previous is not None and previous != catalog:
        raise ValueError("catalog hash conflict")
    state["catalog"][record_hash] = catalog
    state["observations"][identity] = observation
    return True


def rebuild(state: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for observation in state["observations"].values():
        groups.setdefault((observation["site"], observation["answer_id"]), []).append(observation)
    versions: list[dict[str, Any]] = []
    ties: list[dict[str, Any]] = []
    for key in sorted(groups):
        ordered = sorted(
            groups[key],
            key=lambda item: (item["collected_at"], item["record_hash"], item["observation_id"]),
        )
        instants: dict[str, dict[str, Any]] = {}
        for observation in ordered:
            prior = instants.get(observation["collected_at"])
            if prior is not None and prior["record_hash"] != observation["record_hash"]:
                ties.append(observation)
            instants[observation["collected_at"]] = observation
        entity_versions: list[dict[str, Any]] = []
        for observation in instants.values():
            content = dict(state["catalog"][observation["record_hash"]])
            version = {
                "schema_version": CONTENT_SCHEMA,
                "entity_type": ENTITY_TYPE,
                "site": content["site"],
                "answer_id": content["answer_id"],
                "question_id": content["question_id"],
                "creation_at": content["creation_at"],
                "last_edit_at": content["last_edit_at"],
                "owner_user_id": content["owner_user_id"],
                "body_html": content["body_html"],
                "body_text": content["body_text"],
                "code_blocks": list(content["code_blocks"]),
                "content_license": content["content_license"],
                "record_hash": observation["record_hash"],
                "valid_from": observation["collected_at"],
                "valid_to": None,
                "is_current": True,
                "quality": observation["quality"],
                "lineage": observation["lineage"],
            }
            if entity_versions and entity_versions[-1]["record_hash"] == version["record_hash"]:
                continue
            if entity_versions:
                entity_versions[-1]["valid_to"] = version["valid_from"]
                entity_versions[-1]["is_current"] = False
            entity_versions.append(version)
        versions.extend(entity_versions)
    _validate(versions)
    state["versions"] = versions
    return ties


def _validate(versions: list[dict[str, Any]]) -> None:
    current: dict[tuple[str, int], int] = {}
    for version in versions:
        if version["schema_version"] != CONTENT_SCHEMA or version["entity_type"] != ENTITY_TYPE:
            raise ValueError("answer content version schema drifted")
        key = (version["site"], version["answer_id"])
        current[key] = current.get(key, 0) + (1 if version["is_current"] else 0)
        if version["is_current"] != (version["valid_to"] is None):
            raise ValueError("current answer version must have a null valid_to")
        if version["valid_to"] is not None and version["valid_from"] >= version["valid_to"]:
            raise ValueError("answer validity interval must be ordered")
    if any(count != 1 for count in current.values()):
        raise ValueError("each answer must have one current version")
