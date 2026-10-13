"""Bounded supervisor and isolated workers. Run directly; no product CLI coupling."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

# Direct script execution needs both the repository package and the src layout.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from benchmarks.config import PROFILES, SCENARIOS, Config, admit_memory  # noqa: E402
from benchmarks.environment import environment, memory  # noqa: E402
from benchmarks.safety import (  # noqa: E402
    available_disk,
    install_boundary,
    park_inherited_coverage,
    validate_output,
    worker_environment,
)
from benchmarks.scenarios import RESULT_VERSION, run_scenario, tree_bytes  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    for name, field in Config.__dataclass_fields__.items():
        flag = "--" + name.replace("_", "-")
        default = field.default
        if name == "allow_100k":
            result.add_argument(flag, action="store_true")
        elif name == "profile":
            if not isinstance(default, str):
                raise TypeError(name)
            result.add_argument(flag, type=str, default=default, choices=PROFILES)
        elif isinstance(default, bool):
            raise TypeError(name)
        elif isinstance(default, int):
            result.add_argument(flag, type=int, default=default)
        elif isinstance(default, float):
            result.add_argument(flag, type=float, default=default)
        elif isinstance(default, str):
            result.add_argument(flag, type=str, default=default)
        else:
            raise TypeError(name)
    result.add_argument("--output-dir", type=Path, default=ROOT / "data/local/benchmarks")
    result.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    result.add_argument("--suite-timeout-seconds", type=int, default=1800)
    return result


def export(output: Path, document: dict[str, Any]) -> None:
    results = output / "results"
    results.mkdir(parents=True, exist_ok=True)
    json_data = json.dumps(document, sort_keys=True, indent=2, allow_nan=False)
    rows = document["results"]
    flat = [{k: v for k, v in row.items() if not isinstance(v, (dict, list))} for row in rows]
    fields = sorted({key for row in flat for key in row})
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(flat)
    # Each file is atomically replaced; batch_id identifies matching JSON/CSV generations.
    for extension, data in (("json", json_data), ("csv", buffer.getvalue())):
        for name in (document["batch_id"], "latest"):
            path = results / f"{name}.{extension}"
            pending = path.with_suffix(".pending")
            pending.write_text(data, encoding="utf-8")
            pending.replace(path)


def worker(config_path: Path) -> int:
    root = validate_output(config_path.parent)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    config = Config(**payload["config"])
    park_inherited_coverage(root)
    install_boundary(root)

    # Setup and validation also have an RSS limit, not only the measured region.
    def guard() -> None:
        while True:
            _, rss = memory()
            if rss is not None and rss > config.max_peak_mb * 1024**2:
                os._exit(70)
            time.sleep(0.1)

    threading.Thread(target=guard, daemon=True).start()
    try:
        run_scenario(config, payload["scenario"], root / "experiment", payload["iteration"])
        return 0
    except Exception as error:
        (root / "failure.json").write_text(
            json.dumps({"error_type": type(error).__name__, "reason": str(error)[:200]}),
            encoding="utf-8",
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    try:
        config = Config(**{key: getattr(args, key) for key in Config.__dataclass_fields__})
        if not 1 <= args.suite_timeout_seconds <= 7200:
            raise ValueError("suite timeout must be between 1 and 7200 seconds")
        if len(args.scenarios) != len(set(args.scenarios)):
            raise ValueError("scenarios cannot be duplicated")
        output = validate_output(args.output_dir)
        available_disk(output, config.max_disk_mb * 1024**2)
        metadata = environment(ROOT)
        # Git only reads local metadata before the irreversible process boundary.
        metadata["git_revision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        metadata["git_dirty"] = bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
        )
        available = metadata["available_memory_bytes"]
        if available is not None and type(available) is not int:
            raise TypeError("available memory measurement is invalid")
        admit_memory(available, config)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        arguments.error(str(error))
    park_inherited_coverage(output)
    install_boundary(output, supervisor=True)
    batch = str(uuid4())
    batch_root = output / "runs" / batch
    document = {
        "schema_version": RESULT_VERSION,
        "batch_id": batch,
        "environment": metadata,
        "parameters": asdict(config),
        "scenario_order": args.scenarios,
        "suite_timeout_seconds": args.suite_timeout_seconds,
        "results": [],
    }
    started = time.monotonic()
    failed = False
    for iteration in range(1, config.iterations + 1):
        for scenario in args.scenarios:
            root = batch_root / f"iteration={iteration}" / scenario
            root.mkdir(parents=True)
            configuration = root / "config.json"
            configuration.write_text(
                json.dumps(
                    {"config": asdict(config), "scenario": scenario, "iteration": iteration}
                ),
                encoding="utf-8",
            )
            limit = min(
                config.timeout_seconds, args.suite_timeout_seconds - (time.monotonic() - started)
            )
            stop_reason: str | None = None
            print(
                f"start records={config.records} iteration={iteration} scenario={scenario}",
                flush=True,
            )
            with (root / "worker.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--worker", str(configuration)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=worker_environment(),
                )
                deadline = time.monotonic() + max(0, limit)
                try:
                    while process.poll() is None:
                        if time.monotonic() > deadline:
                            stop_reason = "timeout"
                        elif tree_bytes(batch_root) > config.max_disk_mb * 1024**2:
                            stop_reason = "disk_limit"
                        if stop_reason:
                            process.kill()
                            break
                        time.sleep(0.5)
                    process.wait()
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            result_path = root / "experiment" / "result.json"
            if process.returncode == 0 and result_path.exists():
                row = cast(dict[str, Any], json.loads(result_path.read_text(encoding="utf-8")))
            else:
                failure = root / "failure.json"
                row = {
                    "scenario": scenario,
                    "iteration": iteration,
                    "seed": config.seed,
                    "profile": config.profile,
                    "status": "failed",
                    "records_input": config.records,
                    "correctness_passed": False,
                    "elapsed_seconds": None,
                    "records_per_second": None,
                    "peak_memory_mb": None,
                    "error_type": stop_reason
                    or ("memory_limit" if process.returncode == 70 else "worker_failure"),
                    "failure_details": (
                        cast(dict[str, Any], json.loads(failure.read_text(encoding="utf-8")))
                        if failure.exists()
                        else {}
                    ),
                    "worker_exit_code": process.returncode,
                }
                failed = True
            row.update(
                python_version=metadata["python_version"],
                platform=metadata["platform"],
                batch_id=batch,
            )
            document["results"].append(row)
            document["suite_wall_seconds"] = time.monotonic() - started
            export(output, document)
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in (
                            "scenario",
                            "iteration",
                            "status",
                            "elapsed_seconds",
                            "peak_memory_mb",
                            "records_per_second",
                            "error_type",
                        )
                    }
                ),
                flush=True,
            )
            if failed:
                return 1  # Fail closed, retain diagnostics, never continue into larger work.
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(worker(Path(sys.argv[2])))
    raise SystemExit(main())
