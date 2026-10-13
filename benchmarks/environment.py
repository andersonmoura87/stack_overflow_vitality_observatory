"""Best-effort memory telemetry using only the standard library."""

import ctypes
import os
import platform
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path


def memory() -> tuple[int | None, int | None]:
    available: int | None = None
    rss: int | None = None
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total",
                    "available",
                    "page_total",
                    "page_free",
                    "virtual",
                    "free",
                    "ex",
                )
            ]

        class ProcessMemory(ctypes.Structure):
            _fields_ = [("size", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak",
                    "rss",
                    "pool_peak",
                    "pool",
                    "nonpeak",
                    "non",
                    "page",
                    "pagepeak",
                )
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            available = status.available
        process = ProcessMemory()
        process.size = ctypes.sizeof(process)
        # Pseudo-handle -1 avoids truncating a HANDLE on 64-bit Windows.
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(-1), ctypes.byref(process), process.size
        ):
            rss = process.rss
    elif sys.platform.startswith("linux"):
        try:
            values = dict(
                line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
            )
            available = int(values["MemAvailable"].split()[0]) * 1024
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            rss = pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, KeyError):
            pass
    return available, rss


def environment(repository: Path) -> dict[str, str | int | bool | None]:
    available, _ = memory()
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    project_table = project["project"]
    version = project_table["version"]
    if not isinstance(version, str):
        raise TypeError("project version must be a string")
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "available_memory_bytes": available,
        "project_version": version,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
