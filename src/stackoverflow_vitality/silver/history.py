"""Order-independent observations and compressed content history."""

from __future__ import annotations

from typing import Any

from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.silver.models import (
    CONTENT_FIELDS,
    DERIVED_FIELDS,
    SilverQuestion,
    digest,
    validate_history,
)
from stackoverflow_vitality.storage.files import canonical


def empty_state() -> dict[str, Any]:
    return {"catalog": {}, "observations": {}, "questions": []}


def observe(state: dict[str, Any], question: SilverQuestion) -> bool:
    record = to_record(question)
    identity = {
        key: record[key]
        for key in (
            "site",
            "source_run_id",
            "source_page",
            "source_file_checksum",
            "source_item_index",
        )
    }
    observation_id = digest(canonical(identity))
    content = {key: record[key] for key in (*CONTENT_FIELDS, *DERIVED_FIELDS)}
    observation = {
        key: value
        for key, value in record.items()
        if key not in {*CONTENT_FIELDS, *DERIVED_FIELDS, "valid_from", "valid_to", "is_current"}
    }
    observation.update(
        site=question.site, question_id=question.question_id, observation_id=observation_id
    )
    if observation_id in state["observations"]:
        if state["observations"][observation_id] != observation:
            raise ValueError("observation identity conflict")
        return False
    state["catalog"][question.record_hash] = content
    state["observations"][observation_id] = observation
    return True


def rebuild(state: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for observation in state["observations"].values():
        groups.setdefault((observation["site"], observation["question_id"]), []).append(observation)
    history: list[dict[str, Any]] = []
    ties: list[dict[str, Any]] = []
    for key in sorted(groups):
        ordered = sorted(
            groups[key],
            key=lambda item: (
                item["collected_at"],
                item["record_hash"],
                item["observation_id"],
            ),
        )
        # All observations survive; one deterministic winner represents an instant.
        instants: dict[str, dict[str, Any]] = {}
        for observation in ordered:
            prior = instants.get(observation["collected_at"])
            if prior and prior["record_hash"] != observation["record_hash"]:
                ties.append(observation)
            instants[observation["collected_at"]] = observation
        versions: list[dict[str, Any]] = []
        for observation in instants.values():
            record = dict(state["catalog"][observation["record_hash"]])
            record.update(
                {name: value for name, value in observation.items() if name != "observation_id"}
            )
            record.update(valid_from=observation["collected_at"], valid_to=None, is_current=True)
            if versions and versions[-1]["record_hash"] == record["record_hash"]:
                record["valid_from"] = versions[-1]["valid_from"]
                versions[-1] = record
            else:
                if versions:
                    versions[-1]["valid_to"] = record["valid_from"]
                    versions[-1]["is_current"] = False
                versions.append(record)
        history.extend(versions)
    validate_history(history)
    state["questions"] = history
    return ties


def version_keys(records: list[dict[str, Any]]) -> set[tuple[str, int, str, str]]:
    return {
        (item["site"], item["question_id"], item["valid_from"], item["record_hash"])
        for item in records
    }
