"""Small correctness checks only. Performance scales are never run by pytest."""

import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.config import (
    FUNCTIONAL_MAX_PEAK_MB,
    OPERATIONAL_RESERVE_BYTES,
    Config,
    admit_memory,
    host_reserve_bytes,
    required_available_bytes,
)
from benchmarks.generate_bronze import corpus, question, write_corpus
from benchmarks.instrumentation import Timings
from benchmarks.run_silver_benchmark import parser
from benchmarks.safety import (
    ALLOWED,
    REPOSITORY,
    WORKER_SCRIPT,
    authorize_popen,
    park_coverage_controllers,
    validate_output,
    worker_environment,
    worker_launch_allowed,
)
from stackoverflow_vitality.silver import quality


@pytest.fixture
def benchmark_dir():
    ALLOWED.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="test-", dir=ALLOWED) as name:
        yield Path(name)


def test_seed_duplicates_and_profiles():
    config = Config(records=100, duplicate_rate=0.2)
    rows = list(corpus(config))
    assert rows == list(corpus(config))
    assert rows != list(corpus(replace(config, seed=7)))
    assert len({row[0]["question_id"] for row in rows}) == 80
    assert rows[0] == rows[80]
    unique = [row for row, _ in rows[:80]]
    assert sum(row["answer_count"] == 0 for row in unique) == 24
    assert sum("closed_date" in row for row in unique) == 8
    assert sum("link" not in row for row in unique) == 8
    assert all("owner" not in row for row in unique)
    assert len({row["question_id"] for row, _ in corpus(replace(config, duplicate_rate=0))}) == 100


@pytest.mark.parametrize(
    "profile,min_codes,max_codes",
    [("small_text", 0, 1), ("typical_question", 1, 3), ("large_question", 12, 12)],
)
def test_body_profiles(profile, min_codes, max_codes):
    config = Config(records=10, profile=profile)
    for index in range(10):
        row = question(config, index)
        assert min_codes <= row["body"].count("<code>") <= max_codes
        assert len(row["body"]) <= config.max_body_chars
        if profile == "large_question":
            assert len(row["body"]) > config.max_body_chars * 0.85


def test_changes_and_invalid_distributions():
    config = Config(records=100, duplicate_rate=0, missing_optional_rate=0)
    base = [row for row, _ in corpus(config)]
    content = [row for row, _ in corpus(config, "content")]
    metrics = [row for row, _ in corpus(config, "metrics")]
    assert sum(a != b for a, b in zip(base, content, strict=True)) == 10
    assert sum(a != b for a, b in zip(base, metrics, strict=True)) == 25
    for a, b in zip(base, metrics, strict=True):
        assert {k: v for k, v in a.items() if k not in {"score", "view_count", "answer_count"}} == {
            k: v for k, v in b.items() if k not in {"score", "view_count", "answer_count"}
        }
    invalid = [row for row, _ in corpus(config, "invalid")]
    assert sum(a != b for a, b in zip(base, invalid, strict=True)) == 5
    assert sum(old for _, old in corpus(config, "out_of_order")) == 20


@pytest.mark.parametrize(
    "changes",
    [
        {"records": 0},
        {"records": -1},
        {"records": 100001},
        {"records": 50001},
        {"iterations": 0},
        {"iterations": 6},
        {"invalid_rate": -0.1},
        {"duplicate_rate": 1},
        {"metrics_change_rate": 1.1},
        {"invalid_rate": float("nan")},
        {"out_of_order_rate": float("inf")},
        {"profile": "real"},
        {"timeout_seconds": 601},
        {"max_peak_mb": 4096},
        {"max_peak_mb": 0},
        {"max_peak_mb": -1},
        {"max_peak_mb": 10**9},
        {"max_disk_mb": 0},
        {"max_disk_mb": -1},
        {"max_disk_mb": 10**9},
        {"records": 1000, "profile": "large_question"},
    ],
)
def test_invalid_parameters(changes):
    with pytest.raises(ValueError):
        Config(**changes)


def test_explicit_100k_is_supported_but_not_run():
    config = Config(records=100000, allow_100k=True, max_disk_mb=8192)
    assert config.records == 100000
    args = parser().parse_args(["--records", "100000", "--allow-100k", "--max-disk-mb", "8192"])
    assert args.allow_100k


def test_output_path_and_ignored_files(benchmark_dir):
    assert validate_output(benchmark_dir) == benchmark_dir.resolve()
    with pytest.raises(ValueError):
        validate_output(REPOSITORY / "tests")
    with pytest.raises(ValueError):
        validate_output(ALLOWED / ".." / "outside")
    first = write_corpus(Config(records=5), benchmark_dir / "one")
    second = write_corpus(Config(records=5), benchmark_dir / "two")
    assert first == second
    ignored = subprocess.run(["git", "check-ignore", "--quiet", str(benchmark_dir)], cwd=REPOSITORY)
    assert ignored.returncode == 0


def test_instrumentation_restored_and_preserves_result():
    original = quality.clean_body
    text = "<p>Synthetic text</p><code>a = 1\n b</code>"
    expected = original(text)
    timings = Timings()
    with timings.install():
        assert quality.clean_body(text) == expected
    assert quality.clean_body is original
    assert timings.exclusive["code_extract"] > 0
    assert timings.inclusive["parse_clean"] >= timings.exclusive["code_extract"]


def test_process_boundary_blocks_network_and_external_writes(benchmark_dir):
    script = """
import socket, subprocess, sys
from pathlib import Path
from benchmarks.safety import install_boundary
root = Path(sys.argv[1])
install_boundary(root)
for operation in (lambda: socket.socket(), lambda: socket.getaddrinfo('example.invalid', 443),
                  lambda: (root.parent / 'forbidden.txt').write_text('no'),
                  lambda: subprocess.run([sys.executable, '-V'])):
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise AssertionError('boundary escaped')
(root / 'allowed.txt').write_text('synthetic')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(benchmark_dir)],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (benchmark_dir / "allowed.txt").read_text() == "synthetic"
    assert not (benchmark_dir.parent / "forbidden.txt").exists()


def test_all_scenarios_isolated_small_scale(benchmark_dir):
    order = ["B6", "B3", "B2", "B7", "B5", "B4", "B1"]
    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/run_silver_benchmark.py",
            "--records",
            "20",
            "--max-peak-mb",
            str(FUNCTIONAL_MAX_PEAK_MB),
            "--output-dir",
            str(benchmark_dir),
            "--scenarios",
            *order,
            "--max-disk-mb",
            "32",
            "--timeout-seconds",
            "30",
        ],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads((benchmark_dir / "results/latest.json").read_text())
    assert document["schema_version"] == "silver_benchmark.v1"
    assert document["scenario_order"] == order
    assert [row["scenario"] for row in document["results"]] == order
    assert document["parameters"]["records"] == 20
    assert document["parameters"]["max_peak_mb"] == FUNCTIONAL_MAX_PEAK_MB
    rows = {row["scenario"]: row for row in document["results"]}
    assert len(rows) == 7 and all(row["correctness_passed"] for row in rows.values())
    assert rows["B2"]["replay_equal"] and rows["B2"]["metric_observations_created"] == 0
    assert rows["B3"]["content_versions_created"] == 0
    assert rows["B3"]["metric_observations_created"] == 20
    assert rows["B5"]["records_quarantined"] == 1
    assert rows["B4"]["content_versions_created"] == 1
    required = {
        "bronze_input_bytes",
        "silver_output_bytes",
        "quarantine_output_bytes",
        "manifest_bytes",
        "peak_memory_mb",
        "records_per_second",
        "python_version",
        "platform",
        "seed",
        "iteration",
        "bytes_per_input_record",
    }
    assert all(required <= row.keys() for row in rows.values())
    assert all(row["peak_memory_mb"] > 0 and row["elapsed_seconds"] > 0 for row in rows.values())
    assert (benchmark_dir / "results/latest.csv").exists()
    scenarios = {path.name: path for path in (benchmark_dir / "runs").glob("*/iteration=1/*")}
    assert set(scenarios) == set(order)
    baselines = [scenarios[name] / "experiment" / "input" / "baseline" for name in order]
    assert len({path.resolve() for path in baselines}) == 7
    assert all(path.is_dir() for path in baselines)
    for name in ("B3", "B4", "B5", "B6", "B7"):
        batch = scenarios[name] / "experiment" / "input" / "batch"
        assert batch.is_dir() and batch.resolve() != baselines[order.index(name)].resolve()
    assert not (scenarios["B1"] / "experiment" / "input" / "batch").exists()
    assert not (scenarios["B2"] / "experiment" / "input" / "batch").exists()
    logs = list((benchmark_dir / "runs").rglob("worker.log"))
    assert logs and all("PermissionError" not in log.read_text(encoding="utf-8") for log in logs)
    assert all(
        path.resolve().is_relative_to(benchmark_dir.resolve())
        for path in benchmark_dir.rglob(".coverage*")
    )


def _worker_argv(config: Path) -> list[str]:
    return [sys.executable, str(WORKER_SCRIPT), "--worker", str(config)]


def test_worker_policy_accepts_posix_and_windows_forms(benchmark_dir):
    config = benchmark_dir / "spaced dir" / "config.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")
    argv = _worker_argv(config)
    encoded = subprocess.list2cmdline(argv)
    assert worker_launch_allowed(sys.executable, argv, benchmark_dir)
    assert worker_launch_allowed(None, argv, benchmark_dir)
    assert worker_launch_allowed(None, encoded, benchmark_dir)
    assert worker_launch_allowed(sys.executable, encoded, benchmark_dir)
    authorize_popen(None, encoded, supervisor=True, root=benchmark_dir)
    authorize_popen(sys.executable, argv, supervisor=True, root=benchmark_dir)


def test_worker_policy_rejects_other_subprocesses(benchmark_dir):
    config = benchmark_dir / "config.json"
    config.write_text("{}", encoding="utf-8")
    argv = _worker_argv(config)
    encoded = subprocess.list2cmdline(argv)
    outside = REPOSITORY / "config.json"
    other_script = REPOSITORY / "benchmarks" / "safety.py"
    rejected = [
        (None, subprocess.list2cmdline([sys.executable, "-c", "import os"])),
        (
            None,
            subprocess.list2cmdline([sys.executable, str(other_script), "--worker", str(config)]),
        ),
        (
            None,
            subprocess.list2cmdline(
                ["C:\\evil\\python.exe", str(WORKER_SCRIPT), "--worker", str(config)]
            ),
        ),
        (sys.executable, subprocess.list2cmdline(["C:\\evil\\python.exe", *argv[1:]])),
        (None, encoded + " --extra"),
        (None, "cmd.exe /c " + encoded),
        (None, encoded + "\ncalc.exe"),
        (None, '"unterminated'),
        (None, [sys.executable, str(WORKER_SCRIPT), "--worker", str(outside)]),
        (None, [sys.executable, str(WORKER_SCRIPT), "--worker", str(config), "--extra"]),
        (None, [sys.executable, str(WORKER_SCRIPT) + ".evil", "--worker", str(config)]),
        (None, [sys.executable, str(WORKER_SCRIPT), "--Worker", str(config)]),
        (sys.executable, [1, str(WORKER_SCRIPT), "--worker", str(config)]),
        (None, "benchmarks/run_silver_benchmark.py --worker"),
    ]
    for executable, command in rejected:
        assert not worker_launch_allowed(executable, command, benchmark_dir)
        with pytest.raises(PermissionError):
            authorize_popen(executable, command, supervisor=True, root=benchmark_dir)
    with pytest.raises(PermissionError):
        authorize_popen(None, encoded, supervisor=False, root=benchmark_dir)


def test_functional_memory_budget_is_separate_from_operational_limit():
    probe = Config(records=20, max_peak_mb=FUNCTIONAL_MAX_PEAK_MB, max_disk_mb=32)
    operational = Config()
    assert operational.max_peak_mb == 1024
    assert host_reserve_bytes(operational) == OPERATIONAL_RESERVE_BYTES
    assert host_reserve_bytes(probe) < host_reserve_bytes(operational)
    assert required_available_bytes(probe) < OPERATIONAL_RESERVE_BYTES
    admit_memory(required_available_bytes(probe), probe)
    compatible = 400 * 1024**2
    admit_memory(compatible, probe)
    with pytest.raises(ValueError, match="insufficient available memory"):
        admit_memory(compatible, operational)
    with pytest.raises(ValueError, match="insufficient available memory"):
        admit_memory(required_available_bytes(probe) - 1, probe)
    with pytest.raises(ValueError, match="estimated peak exceeds"):
        required_available_bytes(Config(records=1000, max_peak_mb=32, max_disk_mb=4096))
    with pytest.raises(ValueError):
        Config(records=100_000, max_disk_mb=8192)
    assert Config(records=100_000, allow_100k=True, max_disk_mb=8192).records == 100_000
    higher = Config(records=1000, max_peak_mb=2048, max_disk_mb=8192)
    assert required_available_bytes(higher) > required_available_bytes(operational)


def test_worker_environment_and_coverage_stay_inside_root(benchmark_dir, monkeypatch):
    monkeypatch.setenv("COV_CORE_DATAFILE", str(REPOSITORY / ".coverage"))
    monkeypatch.setenv("COVERAGE_PROCESS_START", "cov.ini")
    env = worker_environment()
    assert "COV_CORE_DATAFILE" not in env
    assert "COVERAGE_PROCESS_START" not in env

    class Data:
        _no_disk = False
        _dbs = {}
        _suffix = False

        def __init__(self):
            self._basename = str(REPOSITORY / ".coverage")
            self._filename = self._basename

        def _choose_filename(self):
            self._filename = self._basename

    class Cov:
        def __init__(self):
            self._auto_save = True
            self._data = Data()
            self.config = type("ConfigFile", (), {"data_file": str(REPOSITORY / ".coverage")})()

        def _atexit(self):
            return None

    cov = Cov()
    park_coverage_controllers([cov], benchmark_dir)
    parked = Path(cov._data._filename).resolve()
    assert parked.is_relative_to(benchmark_dir.resolve())
    assert Path(cov.config.data_file).resolve().is_relative_to(benchmark_dir.resolve())
    assert not parked.is_relative_to(REPOSITORY / "src")
    locked = Cov()
    locked._data._dbs = {0: object()}
    park_coverage_controllers([locked], benchmark_dir)
    assert locked._auto_save is False
    assert locked._data._filename == str(REPOSITORY / ".coverage")
