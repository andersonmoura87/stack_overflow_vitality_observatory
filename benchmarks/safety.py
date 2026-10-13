"""Fail-closed local output validation and process-level audit boundary."""

import atexit
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

REPOSITORY = Path(__file__).resolve().parents[1]
ALLOWED = REPOSITORY / "data" / "local" / "benchmarks"
WORKER_SCRIPT = (REPOSITORY / "benchmarks" / "run_silver_benchmark.py").resolve()
_COVERAGE_ENV = (
    "COV_CORE_SOURCE",
    "COV_CORE_CONFIG",
    "COV_CORE_DATAFILE",
    "COV_CORE_BRANCH",
    "COV_CORE_CONTEXT",
    "COVERAGE_PROCESS_START",
)


def validate_output(path: Path) -> Path:
    """Only ignored, untracked paths physically inside the repository's benchmark root."""
    candidate = path.absolute()
    if candidate.drive.startswith("\\\\"):
        raise ValueError("network paths are forbidden")
    for ancestor in (candidate, *candidate.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError("symlinks/junctions are forbidden in output paths")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(ALLOWED) or not resolved.is_relative_to(REPOSITORY):
        raise ValueError("output must stay inside data/local/benchmarks")
    relative = resolved.relative_to(REPOSITORY).as_posix()
    tracked = subprocess.run(
        ["git", "ls-files", "--", relative], cwd=REPOSITORY, capture_output=True, check=True
    )
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", relative + "/probe.json"],
        cwd=REPOSITORY,
        capture_output=True,
    )
    if tracked.stdout or ignored.returncode != 0:
        raise ValueError("output must be ignored by Git and contain no tracked files")
    return resolved


def _same_path(value: object, expected: Path) -> bool:
    if isinstance(value, bytes):
        try:
            value = os.fsdecode(value)
        except (TypeError, ValueError):
            return False
    if not isinstance(value, str | os.PathLike):
        return False
    try:
        return Path(value).resolve() == expected.resolve()
    except OSError:
        return False


def split_windows_command(command: str) -> list[str] | None:
    """Parse a CreateProcess command line. Unbalanced quotes fail closed."""
    if any(char in command for char in "\n\r\x00"):
        return None
    args: list[str] = []
    index = 0
    length = len(command)
    while index < length:
        while index < length and command[index] in " \t":
            index += 1
        if index >= length:
            break
        quoted = False
        token: list[str] = []
        while index < length:
            char = command[index]
            if char == "\\":
                start = index
                while index < length and command[index] == "\\":
                    index += 1
                slashes = index - start
                if index < length and command[index] == '"':
                    token.append("\\" * (slashes // 2))
                    if slashes % 2:
                        token.append('"')
                        index += 1
                    else:
                        quoted = not quoted
                        index += 1
                else:
                    token.append("\\" * slashes)
                continue
            if char == '"':
                quoted = not quoted
                index += 1
                continue
            if char in " \t" and not quoted:
                break
            token.append(char)
            index += 1
        if quoted:
            return None
        args.append("".join(token))
    return args


def command_tokens(command: object) -> list[str] | None:
    if isinstance(command, list | tuple):
        if not command or not all(isinstance(item, str | os.PathLike) for item in command):
            return None
        return [os.fsdecode(item) for item in command]
    if isinstance(command, str):
        return split_windows_command(command) if command.strip() else None
    return None


def worker_launch_allowed(executable: object, command: object, root: Path) -> bool:
    """True only for this interpreter, this worker entrypoint, and one local config."""
    tokens = command_tokens(command)
    if tokens is None or len(tokens) != 4:
        return False
    program, script, flag, config = tokens
    if flag != "--worker" or Path(config).name != "config.json":
        return False
    if not _same_path(program, Path(sys.executable)):
        return False
    if executable is not None and not _same_path(executable, Path(sys.executable)):
        return False
    if not _same_path(script, WORKER_SCRIPT):
        return False
    config_path = Path(config)
    if not config_path.is_absolute():
        return False
    try:
        resolved = config_path.resolve()
        approved = root.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(approved) and resolved != approved


def authorize_popen(executable: object, command: object, *, supervisor: bool, root: Path) -> None:
    if not supervisor or not worker_launch_allowed(executable, command, root):
        raise PermissionError("only an isolated benchmark worker may be launched")


def worker_environment() -> dict[str, str]:
    """Child env without the coverage hooks that write outside the approved root."""
    env = os.environ.copy()
    for key in _COVERAGE_ENV:
        env.pop(key, None)
    return env


class _CoverageConfig(Protocol):
    data_file: str


class _CoverageData(Protocol):
    _no_disk: bool
    _dbs: Mapping[int, object]
    _basename: str
    _filename: str

    def _choose_filename(self) -> None: ...


class _CoverageController(Protocol):
    _auto_save: bool
    _data: _CoverageData | None
    config: _CoverageConfig

    def _atexit(self, event: str = ...) -> None: ...


def _as_controller(value: object) -> _CoverageController | None:
    config = getattr(value, "config", None)
    data_file = getattr(config, "data_file", None)
    if not isinstance(data_file, str) or not callable(getattr(value, "_atexit", None)):
        return None
    return cast(_CoverageController, value)


def _coverage_controllers() -> list[_CoverageController]:
    found: list[object] = []
    embed = sys.modules.get("pytest_cov.embed")
    if embed is not None:
        active = getattr(embed, "_active_cov", None)
        if active is not None:
            found.append(active)
    coverage_mod = sys.modules.get("coverage")
    startup = getattr(coverage_mod, "process_startup", None) if coverage_mod is not None else None
    auto = getattr(startup, "coverage", None)
    if auto is not None:
        found.append(auto)
    control = sys.modules.get("coverage.control")
    instances = getattr(getattr(control, "Coverage", None), "_instances", ())
    if isinstance(instances, list | tuple):
        found.extend(instances)
    unique: list[_CoverageController] = []
    seen: set[int] = set()
    for item in found:
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        controller = _as_controller(item)
        if controller is not None:
            unique.append(controller)
    return unique


def _drop_coverage_exit(cov: _CoverageController) -> None:
    try:
        atexit.unregister(cov._atexit)
    except (TypeError, ValueError):
        return


def park_coverage_controllers(controllers: list[_CoverageController], root: Path) -> None:
    """Point these collectors at an ignored file inside root, or stop their disk flush."""
    if not controllers:
        return
    approved = root.resolve()
    destination = approved / "coverage" / ".coverage"
    if not destination.is_relative_to(approved):
        raise PermissionError("coverage park escaped approved root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    parked = os.path.abspath(destination)
    for cov in controllers:
        data = cov._data
        writable = True
        if data is not None and not data._no_disk:
            if data._dbs:
                writable = False
            else:
                data._basename = parked
                data._choose_filename()
                try:
                    writable = Path(data._filename).resolve().is_relative_to(approved)
                except OSError:
                    writable = False
        if writable:
            cov.config.data_file = parked
        else:
            cov._auto_save = False
            _drop_coverage_exit(cov)


def park_inherited_coverage(root: Path) -> None:
    """Retarget coverage already started in this process; never widen the write root."""
    park_coverage_controllers(_coverage_controllers(), root)


def install_boundary(root: Path, *, supervisor: bool = False) -> None:
    """Irreversible within a worker: deny networking, child processes and external writes."""
    root = root.resolve()
    sys.dont_write_bytecode = True

    def allowed(value: Any) -> None:
        if isinstance(value, int):  # fd of a previously validated open/mkstemp
            return
        if value is None or not Path(os.fsdecode(value)).resolve().is_relative_to(root):
            raise PermissionError("benchmark write outside approved root")

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith("socket.") or event in {"os.system", "os.fork", "os.exec"}:
            raise PermissionError("network and subprocesses are forbidden in benchmark worker")
        if event == "subprocess.Popen":
            executable = args[0] if args else None
            command = args[1] if len(args) > 1 else None
            authorize_popen(executable, command, supervisor=supervisor, root=root)
        if event == "open":
            path, mode, flags = args
            if (mode and any(char in mode for char in "wax+")) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            ):
                allowed(path)
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime"}:
            allowed(args[0])
        elif event in {"os.rename", "os.link"}:
            allowed(args[0])
            allowed(args[1])
        elif event == "os.symlink":
            raise PermissionError("symlinks are forbidden in benchmark worker")

    sys.addaudithook(audit)


def available_disk(root: Path, required: int) -> None:
    ancestor = root
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < required + 512 * 1024**2:
        raise ValueError("insufficient free disk including 512 MiB reserve")
