"""Offline orchestration with a manifest as the atomic generation commit."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.silver.history import observe, rebuild, version_keys
from stackoverflow_vitality.silver.models import (
    SCHEMA_VERSION,
    QualityEvent,
    TransformationManifest,
)
from stackoverflow_vitality.silver.ports import (
    BronzeReader,
    QuarantineStore,
    SilverStore,
    TransformationManifestStore,
)
from stackoverflow_vitality.silver.quality import QualityConfig, normalize

LOGGER = logging.getLogger(__name__)


def transform_questions(
    reader: BronzeReader,
    silver: SilverStore,
    quarantine: QuarantineStore,
    manifests: TransformationManifestStore,
    *,
    source_run_id: str | None = None,
    transformation_run_id: str | None = None,
    config: QualityConfig | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> TransformationManifest:
    run_id = transformation_run_id or str(uuid4())
    config = config or QualityConfig()
    UUID(run_id)
    if source_run_id is not None:
        UUID(source_run_id)
    started = now()
    if started.tzinfo is None:
        raise ValueError("clock must supply timezone")
    start_tick = monotonic()
    sources: set[str] = set()
    inputs: dict[str, str] = {}
    outputs: dict[str, str] = {}
    hashes: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counters = dict(
        input_pages=0,
        input_records=0,
        accepted_records=0,
        quarantined_records=0,
        unchanged_records=0,
        new_versions=0,
        closed_versions=0,
        quality_errors=0,
        quality_warnings=0,
    )
    sequence = 0
    stage = "started"

    def result(status: str, error_type: str | None = None) -> TransformationManifest:
        return TransformationManifest(
            transformation_run_id=run_id,
            dataset_id=silver.dataset_id,
            commit_sequence=sequence,
            source_run_ids=tuple(sorted(sources)),
            started_at=started,
            finished_at=now(),
            status=status,
            schema_version=SCHEMA_VERSION,
            input_checksums=dict(sorted(inputs.items())),
            output_checksums=hashes.copy(),
            output_paths=outputs.copy(),
            failure_stage=stage if error_type else None,
            error_type=error_type,
            input_pages=counters["input_pages"],
            input_records=counters["input_records"],
            accepted_records=counters["accepted_records"],
            quarantined_records=counters["quarantined_records"],
            unchanged_records=counters["unchanged_records"],
            new_versions=counters["new_versions"],
            closed_versions=counters["closed_versions"],
            quality_errors=counters["quality_errors"],
            quality_warnings=counters["quality_warnings"],
        )

    def log(status: str, error_type: str | None = None) -> None:
        LOGGER.info(
            json.dumps(
                {
                    "event": "silver_transformation",
                    "transformation_run_id": run_id,
                    "source_run_id": source_run_id,
                    "schema_version": SCHEMA_VERSION,
                    "duration_ms": max(0, int((monotonic() - start_tick) * 1000)),
                    "status": status,
                    "error_type": error_type,
                    **counters,
                },
                sort_keys=True,
            )
        )

    log("started")
    try:
        with silver.lock():
            stage = "load_previous"
            previous = manifests.latest(silver.dataset_id)
            state = silver.load(previous)
            sequence = 1 if previous is None else previous["commit_sequence"] + 1
            old_versions = version_keys(state["questions"])
            old_closed = version_keys([row for row in state["questions"] if not row["is_current"]])
            stage = "transforming"
            for page in reader.pages(source_run_id):
                counters["input_pages"] += 1
                inputs[page.path] = page.file_checksum
                source = page.envelope.get("run_id")
                if isinstance(source, str):
                    sources.add(source)
                for index, item in enumerate(page.items):
                    counters["input_records"] += 1
                    accepted, found = normalize(item, page, index, run_id, started, config)
                    events.extend(to_record(event) for event in found)
                    counters["quality_errors"] += sum(event.severity == "error" for event in found)
                    counters["quality_warnings"] += sum(
                        event.severity == "warning" for event in found
                    )
                    if accepted is None:
                        counters["quarantined_records"] += 1
                        # One quarantine entry per invalid item; all rule failures remain in events.
                        rejected.append(
                            to_record(next(event for event in found if event.severity == "error"))
                        )
                        continue
                    counters["accepted_records"] += 1
                    known_content = accepted.record_hash in state["catalog"]
                    added = observe(state, accepted)
                    if not added or known_content:
                        counters["unchanged_records"] += 1
            for observation in rebuild(state):
                event = QualityEvent(
                    transformation_run_id=run_id,
                    source_run_id=observation["source_run_id"],
                    source_page=observation["source_page"],
                    question_id=observation["question_id"],
                    rule_id="SQ-014",
                    severity="warning",
                    reason="conflicting content at same observation time",
                    field="collected_at",
                    observed_value="timestamp tie",
                    quarantined_at=started,
                    source_path=observation["source_path"],
                    source_item_index=observation["source_item_index"],
                )
                events.append(to_record(event))
                counters["quality_warnings"] += 1
            counters["new_versions"] = len(version_keys(state["questions"]) - old_versions)
            counters["closed_versions"] = len(
                version_keys([row for row in state["questions"] if not row["is_current"]])
                - old_closed
            )
            stage = "silver_write"
            silver_paths, silver_hashes = silver.write(state)
            silver.confirm(silver_paths, silver_hashes)
            outputs.update(silver_paths)
            hashes.update(silver_hashes)
            stage = "quarantine_write"
            quarantine_paths, quarantine_hashes = quarantine.write(run_id, rejected, events)
            quarantine.confirm(quarantine_paths, quarantine_hashes)
            outputs.update(quarantine_paths)
            hashes.update(quarantine_hashes)
            stage = "output_confirmation"
            silver.confirm(silver_paths, silver_hashes)
            quarantine.confirm(quarantine_paths, quarantine_hashes)
            log("outputs_persisted")
            stage = "manifest_terminal"
            terminal = result("empty" if counters["input_records"] == 0 else "completed")
            manifests.write(terminal)
            log(terminal.status)
            return terminal
    except Exception as error:
        failed = result("failed", type(error).__name__)
        # A terminal publication error may prevent even the failure artifact. Never overwrite it.
        try:
            manifests.write(failed)
        except Exception:
            failed = replace(failed, failure_stage="manifest_terminal")
        log("failed", type(error).__name__)
        return failed
