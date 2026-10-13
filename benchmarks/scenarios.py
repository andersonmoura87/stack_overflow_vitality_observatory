"""Independent B1-B7 experiments with functional assertions outside timed sections."""

import gc
import json
import threading
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from benchmarks.config import Config
from benchmarks.environment import memory
from benchmarks.generate_bronze import NOW, selected, write_corpus
from benchmarks.instrumentation import TimedReader, Timings
from stackoverflow_vitality.silver.history import empty_state, observe, rebuild
from stackoverflow_vitality.silver.models import TransformationManifest, validate_history
from stackoverflow_vitality.silver.ports import BronzeReader
from stackoverflow_vitality.silver.quality import QualityConfig, normalize
from stackoverflow_vitality.silver.storage import (
    LocalBronzeReader,
    LocalQuarantineStore,
    LocalSilverStore,
    LocalTransformationManifestStore,
)
from stackoverflow_vitality.silver.transform import transform_questions

RESULT_VERSION = "silver_benchmark.v1"
VARIANTS = {
    "B1": "base",
    "B2": "base",
    "B3": "metrics",
    "B4": "content",
    "B5": "invalid",
    "B6": "out_of_order",
    "B7": "base",
}


def transform(
    root: Path,
    bronze: Path,
    label: str,
    config: Config,
    timed: Timings | None = None,
) -> TransformationManifest:
    silver = LocalSilverStore(root / "silver")
    manifests = LocalTransformationManifestStore(root / "silver" / "manifests")
    reader: BronzeReader = LocalBronzeReader(bronze)
    if timed is not None:
        reader = TimedReader(reader, timed)
    return transform_questions(
        reader,
        silver,
        LocalQuarantineStore(root / "quarantine"),
        manifests,
        transformation_run_id=str(uuid5(NAMESPACE_URL, f"benchmark:{config.seed}:{label}")),
        now=lambda: NOW,
        config=QualityConfig(max_body_chars=config.max_body_chars),
    )


def load(root: Path) -> dict[str, Any]:
    silver = LocalSilverStore(root / "silver")
    manifests = LocalTransformationManifestStore(root / "silver" / "manifests")
    return silver.load(manifests.latest(silver.dataset_id))


def tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def run_scenario(config: Config, scenario: str, root: Path, iteration: int) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    baseline = root / "input" / "baseline"
    setup_start = time.perf_counter()
    initial = write_corpus(config, baseline)
    before_observations = before_versions = 0
    baseline_hashes: dict[str, str] | None = None
    baseline_mtimes: dict[str, int] | None = None
    if scenario != "B1":
        first = transform(root, baseline, "baseline", config)
        if first.status != "completed":
            raise RuntimeError("baseline failed: " + str(first.error_type))
        baseline_hashes = {
            key: first.output_checksums[key] for key in ("catalog", "observations", "questions")
        }
        baseline_mtimes = {
            key: Path(first.output_paths[key]).stat().st_mtime_ns for key in baseline_hashes
        }
        state = load(root)
        before_observations = len(state["observations"])
        before_versions = len(state["questions"])
        del state
    bronze = baseline
    input_info = initial
    if scenario not in {"B1", "B2"}:
        bronze = root / "input" / "batch"
        # B7 uses unchanged questions at a NEW observation time/source, exercising state recovery.
        variant = VARIANTS[scenario] if scenario != "B7" else "recovery"
        input_info = write_corpus(config, bronze, variant)
    gc.collect()
    setup_seconds = time.perf_counter() - setup_start
    timings = Timings()
    stop = threading.Event()
    rss_values = []
    violation = []

    def sample() -> None:
        while not stop.is_set():
            _, rss = memory()
            if rss is not None:
                rss_values.append(rss)
            peak = tracemalloc.get_traced_memory()[1]
            if max(peak, rss or 0) > config.max_peak_mb * 1024**2:
                violation.append("memory_limit")
                # Worker process only; parent records nonzero exit and preserves artifacts.
                __import__("os")._exit(70)
            stop.wait(0.05)

    tracemalloc.start(1)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    start = time.perf_counter()
    try:
        with timings.install():
            terminal = transform(root, bronze, "measured", config, timings)
        elapsed = time.perf_counter() - start
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        stop.set()
        sampler.join()
        tracemalloc.stop()
    validation_start = time.perf_counter()
    if terminal.status != "completed":
        raise RuntimeError("transformation failed: " + str(terminal.error_type))
    state = load(root)
    validate_history(state["questions"])
    expected_invalid = int(config.records * config.invalid_rate) if scenario == "B5" else 0
    assert terminal.input_records == config.records
    assert terminal.quarantined_records == expected_invalid
    assert terminal.accepted_records == config.records - expected_invalid
    assert len(state["observations"]) - before_observations == (
        0 if scenario == "B2" else terminal.accepted_records
    )
    assert len({(q["site"], q["question_id"]) for q in state["questions"] if q["is_current"]}) == (
        config.unique_records
    )
    replay_equal: bool | None = None
    if scenario == "B2":
        if baseline_hashes is None or baseline_mtimes is None:
            raise RuntimeError("replay baseline missing")
        replay_equal = all(terminal.output_checksums[k] == v for k, v in baseline_hashes.items())
        assert replay_equal and terminal.new_versions == 0
        assert all(
            Path(terminal.output_paths[k]).stat().st_mtime_ns == value
            for k, value in baseline_mtimes.items()
        )
    if scenario in {"B3", "B5", "B7"}:
        assert terminal.new_versions == 0 and len(state["questions"]) == before_versions
    if scenario == "B4":
        changed = len(selected(config, "content_change", config.content_change_rate))
        assert terminal.new_versions == changed and terminal.closed_versions == changed
    if scenario == "B6":
        # Independent chronological ingestion of identical page observations must converge.
        expected = empty_state()
        pages = [*LocalBronzeReader(baseline).pages(), *LocalBronzeReader(bronze).pages()]
        for page in sorted(pages, key=lambda p: p.envelope["recovered_at"]):
            for index, item in enumerate(page.items):
                record, _ = normalize(
                    item,
                    page,
                    index,
                    "reference",
                    NOW,
                    QualityConfig(max_body_chars=config.max_body_chars),
                )
                assert record is not None
                observe(expected, record)
        rebuild(expected)
        assert expected == state
        assert all(
            not row["title_raw"].endswith("earlier synthetic revision")
            for row in state["questions"]
            if row["is_current"]
        )
    observations_created = len(state["observations"]) - before_observations
    del state
    silver_bytes = sum(
        Path(terminal.output_paths[k]).stat().st_size
        for k in ("catalog", "observations", "questions")
    )
    quarantine_bytes = sum(
        Path(terminal.output_paths[k]).stat().st_size
        for k in ("quarantined_records", "quality_events")
    )
    manifest_bytes = (
        (root / "silver" / "manifests" / f"{terminal.transformation_run_id}.json").stat().st_size
    )
    result: dict[str, object] = {
        "schema_version": RESULT_VERSION,
        "scenario": scenario,
        "status": "completed",
        "records_input": terminal.input_records,
        "records_accepted": terminal.accepted_records,
        "records_quarantined": terminal.quarantined_records,
        "content_versions_created": terminal.new_versions,
        "content_versions_closed": terminal.closed_versions,
        "metric_observations_created": observations_created,
        "unchanged_records": terminal.unchanged_records,
        "elapsed_seconds": elapsed,
        "records_per_second": config.records / elapsed,
        "peak_memory_mb": peak / 1024**2,
        "peak_rss_mb": max(rss_values, default=0) / 1024**2 if rss_values else None,
        "bronze_input_bytes": input_info["bytes"],
        "silver_output_bytes": silver_bytes,
        "quarantine_output_bytes": quarantine_bytes,
        "manifest_bytes": manifest_bytes,
        "bytes_per_input_record": (silver_bytes + quarantine_bytes + manifest_bytes)
        / config.records,
        "retained_dataset_bytes": tree_bytes(root),
        "corpus_sha256": input_info["sha256"],
        "seed": config.seed,
        "iteration": iteration,
        "profile": config.profile,
        "parameters": asdict(config),
        "correctness_passed": True,
        "replay_equal": replay_equal,
        "setup_seconds": setup_seconds,
        "validation_seconds": time.perf_counter() - validation_start,
        "exclusive_seconds": dict(sorted(timings.exclusive.items())),
        "inclusive_seconds": dict(sorted(timings.inclusive.items())),
    }
    for name in (
        "bronze_read",
        "parse_clean",
        "code_extract",
        "quality",
        "history",
        "rebuild",
        "serialization",
        "persistence",
        "manifest",
        "load_existing",
        "generation",
    ):
        result[name + "_seconds"] = timings.exclusive[name]
    (root / "result.json").write_text(
        json.dumps(result, sort_keys=True, indent=2), encoding="utf-8"
    )
    return result
