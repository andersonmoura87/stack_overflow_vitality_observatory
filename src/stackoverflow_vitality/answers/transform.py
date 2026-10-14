"""Bronze answers to historical Silver. Question Silver is only read."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from stackoverflow_vitality.answers.bronze import answers_root
from stackoverflow_vitality.answers.history import empty_state, observe, rebuild
from stackoverflow_vitality.answers.models import SCHEMA_VERSION, payload_checksum
from stackoverflow_vitality.answers.quality import normalize_answer
from stackoverflow_vitality.silver.storage import json_lines, write_immutable
from stackoverflow_vitality.storage.files import IntegrityError, canonical, exclusive_lock

Clock = Callable[[], datetime]


class AnswerTransformFailure(RuntimeError):
    """A page, path or publication failure. Item defects do not use this type."""


@dataclass(frozen=True)
class AnswerTransformResult:
    transformation_run_id: str
    status: str
    pages_read: int
    answers_published: int
    observations: int
    quarantined_records: int
    warnings: int
    reconciliation: str


def transform_answers(
    bronze: Path,
    silver: Path,
    quarantine: Path,
    *,
    site: str | None = None,
    run_id: str | None = None,
    questions_silver: Path | None = None,
    dry_run: bool = False,
    max_body_chars: int = 500_000,
    now: Clock = lambda: datetime.now(UTC),
    transformation_run_id: str | None = None,
) -> AnswerTransformResult:
    if max_body_chars < 1:
        raise ValueError("max_body_chars must be positive")
    _directories(bronze, silver, quarantine)
    pages = list(_pages(bronze, site, run_id))
    run = transformation_run_id or "dry-run"
    if dry_run:
        return AnswerTransformResult(
            transformation_run_id=run,
            status="dry_run",
            pages_read=len(pages),
            answers_published=0,
            observations=0,
            quarantined_records=0,
            warnings=0,
            reconciliation="skipped" if questions_silver is None else "planned",
        )
    run = transformation_run_id or str(uuid4())
    moment = now()
    state = empty_state()
    quality: list[dict[str, Any]] = []
    warnings = 0
    try:
        for _path, envelope in pages:
            payload = envelope["payload"]
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise AnswerTransformFailure("answer page has no items array")
            source_run = envelope.get("run_id")
            source_run_id = source_run if isinstance(source_run, str) else None
            for index, item in enumerate(items):
                record, events = normalize_answer(
                    item, envelope, index, now=moment, max_body_chars=max_body_chars
                )
                for event in events:
                    quality.append(event.record(run, source_run_id))
                    if event.severity == "warning":
                        warnings += 1
                if record is None:
                    continue
                try:
                    observe(state, record)
                except ValueError:
                    quality.append(
                        {
                            "schema_version": "silver_answer_quality.v1",
                            "transformation_run_id": run,
                            "source_run_id": source_run_id,
                            "rule_id": "SA-009",
                            "severity": "error",
                            "field": "record_hash",
                            "reason": "conflicting duplicate observation",
                            "answer_id": record["answer_id"],
                            "question_id": record["question_id"],
                            "source_item_index": index,
                        }
                    )
        ties = rebuild(state)
    except AnswerTransformFailure:
        raise
    except (ValueError, IntegrityError) as error:
        raise AnswerTransformFailure(str(error)) from error
    for tie in ties:
        warnings += 1
        quality.append(
            {
                "schema_version": "silver_answer_quality.v1",
                "transformation_run_id": run,
                "source_run_id": tie["lineage"]["source_run_id"],
                "rule_id": "SA-016",
                "severity": "warning",
                "field": "collected_at",
                "reason": "divergent answer content at the same collected_at",
                "answer_id": tie["answer_id"],
                "question_id": tie["question_id"],
                "source_item_index": tie["lineage"]["source_item_index"],
            }
        )
    reconciliation, extra = _reconcile(questions_silver, state)
    warnings += len(extra)
    catalog = [state["catalog"][key] for key in sorted(state["catalog"])]
    observations = [state["observations"][key] for key in sorted(state["observations"])]
    versions = list(state["versions"])
    known = set(state["catalog"])
    if any(item["record_hash"] not in known for item in [*versions, *observations]):
        raise AnswerTransformFailure("answer catalog referential integrity failed")
    errors = sum(1 for item in quality if item["severity"] == "error")
    if not versions and errors == 0:
        status = "empty"
    elif errors or extra:
        status = "partial"
    else:
        status = "completed"
    result = AnswerTransformResult(
        transformation_run_id=run,
        status=status,
        pages_read=len(pages),
        answers_published=len({(item["site"], item["answer_id"]) for item in versions}),
        observations=len(observations),
        quarantined_records=errors,
        warnings=warnings,
        reconciliation=reconciliation,
    )
    _publish(silver, quarantine, catalog, observations, versions, quality, extra, result, moment)
    return result


def _directories(bronze: Path, silver: Path, quarantine: Path) -> None:
    for path in (bronze, silver, quarantine):
        if not path.is_dir():
            raise ValueError("bronze, silver and quarantine paths must be existing directories")
    if len({path.resolve() for path in (bronze, silver, quarantine)}) != 3:
        raise ValueError("bronze, silver and quarantine paths must not overlap")


def _pages(bronze: Path, site: str | None, run_id: str | None) -> list[tuple[Path, dict[str, Any]]]:
    root = answers_root(bronze)
    if not root.exists():
        return []
    found: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("site=*/run=*/b=*/page=*.json")):
        path.resolve().relative_to(bronze.resolve())
        try:
            loaded = json.loads(path.read_bytes())
        except (OSError, ValueError) as error:
            raise AnswerTransformFailure(f"unreadable answer page: {path.name}") from error
        if not isinstance(loaded, dict) or loaded.get("schema_version") != SCHEMA_VERSION:
            raise AnswerTransformFailure(f"answer schema drift: {path.name}")
        payload = loaded.get("payload")
        if not isinstance(payload, dict) or payload_checksum(payload) != loaded.get("checksum"):
            raise AnswerTransformFailure(f"answer page checksum mismatch: {path.name}")
        if site is not None and loaded.get("site") != site:
            continue
        if run_id is not None and loaded.get("run_id") != run_id:
            continue
        found.append((path, loaded))
    return found


def _reconcile(
    questions_silver: Path | None, state: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    if questions_silver is None:
        return "skipped", []
    if not questions_silver.is_file():
        raise AnswerTransformFailure("question silver file is missing")
    accepted = _question_acceptance(questions_silver)
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    for observation in state["observations"].values():
        key = (observation["site"], observation["answer_id"])
        current = latest.get(key)
        rank = (
            observation["collected_at"],
            observation["record_hash"],
            observation["observation_id"],
        )
        if current is None or rank >= (
            current["collected_at"],
            current["record_hash"],
            current["observation_id"],
        ):
            latest[key] = observation
    by_question: dict[tuple[str, int], list[dict[str, Any]]] = {}
    events: list[dict[str, Any]] = []
    collected_ids = {(item["site"], item["answer_id"]) for item in latest.values()}
    for observation in latest.values():
        by_question.setdefault((observation["site"], observation["question_id"]), []).append(
            observation
        )
        expected = accepted.get((observation["site"], observation["question_id"]))
        if (observation["site"], observation["question_id"]) not in accepted:
            continue
        if expected == observation["answer_id"] and observation["is_accepted"] is False:
            events.append(_divergence(observation, "SA-011", "question acceptance disagrees"))
        if observation["is_accepted"] is True and expected not in {None, observation["answer_id"]}:
            events.append(_divergence(observation, "SA-011", "answer acceptance disagrees"))
    for group in by_question.values():
        accepted_rows = [row for row in group if row["is_accepted"] is True]
        if len(accepted_rows) > 1:
            events.append(_divergence(accepted_rows[0], "SA-012", "multiple accepted answers"))
    for (site, question_id), answer_id in sorted(accepted.items()):
        if type(answer_id) is int and (site, answer_id) not in collected_ids:
            events.append(
                {
                    "schema_version": "silver_answer_quality.v1",
                    "rule_id": "SA-013",
                    "severity": "warning",
                    "field": "accepted_answer_id",
                    "reason": "accepted answer was not collected; coverage may be incomplete",
                    "answer_id": answer_id,
                    "question_id": question_id,
                    "source_item_index": None,
                }
            )
    return "compared", events


def _question_acceptance(path: Path) -> dict[tuple[str, int], int | None]:
    found: dict[tuple[str, int], int | None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or record.get("is_current") is not True:
            continue
        site = record.get("site")
        question_id = record.get("question_id")
        accepted_id = record.get("accepted_answer_id")
        if isinstance(site, str) and type(question_id) is int:
            if accepted_id is None or type(accepted_id) is int:
                found[(site, question_id)] = accepted_id
    return found


def _divergence(observation: dict[str, Any], rule: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "silver_answer_quality.v1",
        "rule_id": rule,
        "severity": "warning",
        "field": "is_accepted",
        "reason": reason,
        "answer_id": observation["answer_id"],
        "question_id": observation["question_id"],
        "source_item_index": observation["lineage"]["source_item_index"],
    }


def _publish(
    silver: Path,
    quarantine: Path,
    catalog: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    quality: list[dict[str, Any]],
    reconciliation: list[dict[str, Any]],
    result: AnswerTransformResult,
    moment: datetime,
) -> None:
    catalog_bytes = json_lines(catalog)
    observation_bytes = json_lines(observations)
    version_bytes = json_lines(versions)
    generation = hashlib.sha256(catalog_bytes + observation_bytes + version_bytes).hexdigest()
    base = silver / "answers" / f"generation={generation}"
    with exclusive_lock(silver / "answers" / ".transform.lock"):
        write_immutable(base / "catalog.jsonl", catalog_bytes)
        write_immutable(base / "observations.jsonl", observation_bytes)
        write_immutable(base / "content_versions.jsonl", version_bytes)
        for path, data in (
            (base / "catalog.jsonl", catalog_bytes),
            (base / "observations.jsonl", observation_bytes),
            (base / "content_versions.jsonl", version_bytes),
        ):
            if path.read_bytes() != data:
                raise IntegrityError("answer silver checksum confirmation failed")
        quarantine_dir = (
            quarantine / "answers" / f"transformation_run_id={result.transformation_run_id}"
        )
        write_immutable(quarantine_dir / "items.jsonl", json_lines(quality))
        write_immutable(quarantine_dir / "reconciliation.jsonl", json_lines(reconciliation))
        manifest = {
            "schema_version": "silver_answer_transformation.v1",
            "transformation_run_id": result.transformation_run_id,
            "status": result.status,
            "generation": generation,
            "pages_read": result.pages_read,
            "answers_published": result.answers_published,
            "observations": result.observations,
            "quarantined_records": result.quarantined_records,
            "warnings": result.warnings,
            "reconciliation": result.reconciliation,
            "finished_at": moment.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "coverage_complete": result.quarantined_records == 0 and not reconciliation,
        }
        write_immutable(
            silver / "answers" / "manifests" / f"{result.transformation_run_id}.json",
            canonical(manifest),
        )
