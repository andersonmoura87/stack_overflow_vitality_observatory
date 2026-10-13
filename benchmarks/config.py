"""Explicit experiment parameters and conservative resource bounds."""

import math
from dataclasses import dataclass

STANDARD_SCALES = (1_000, 10_000, 50_000)
PROFILES = ("small_text", "typical_question", "large_question")
SCENARIOS = tuple(f"B{i}" for i in range(1, 8))
# Real runs keep the historical ceiling. The 20-record probe opts into a smaller cap.
OPERATIONAL_RESERVE_BYTES = 512 * 1024**2
FUNCTIONAL_MAX_RECORDS = 20
FUNCTIONAL_MAX_PEAK_MB = 256
FUNCTIONAL_RESERVE_BYTES = 32 * 1024**2


@dataclass(frozen=True)
class Config:
    records: int = 1000
    profile: str = "typical_question"
    seed: int = 20260921
    iterations: int = 1
    invalid_rate: float = 0.05
    duplicate_rate: float = 0.1
    content_change_rate: float = 0.1
    metrics_change_rate: float = 0.25
    out_of_order_rate: float = 0.2
    unanswered_rate: float = 0.3
    closed_rate: float = 0.1
    missing_optional_rate: float = 0.1
    allow_100k: bool = False
    max_body_chars: int = 500_000
    timeout_seconds: int = 300
    max_peak_mb: int = 1024
    max_disk_mb: int = 4096

    def __post_init__(self) -> None:
        if type(self.records) is not int or not 1 <= self.records <= 100_000:
            raise ValueError("records must be between 1 and 100000")
        if self.records > 50_000 and not self.allow_100k:
            raise ValueError("records above 50000 require --allow-100k")
        if not 1 <= self.iterations <= 5:
            raise ValueError("iterations must be between 1 and 5")
        if self.profile not in PROFILES:
            raise ValueError("unknown corpus profile")
        for name in self.__dataclass_fields__:
            if name.endswith("_rate"):
                value = getattr(self, name)
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"{name} must be in [0, 1]")
        if self.duplicate_rate == 1:
            raise ValueError("duplicate_rate must leave at least one unique record")
        if not 1024 <= self.max_body_chars <= 500_000:
            raise ValueError("max_body_chars must be between 1024 and 500000")
        if not 1 <= self.timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 1 and 600")
        if type(self.max_peak_mb) is not int or not 32 <= self.max_peak_mb <= 2048:
            raise ValueError("memory/disk limit outside safe bounds")
        if type(self.max_disk_mb) is not int or not 32 <= self.max_disk_mb <= 8192:
            raise ValueError("memory/disk limit outside safe bounds")
        # Seven independent datasets + baseline + full JSON generations. Deliberately conservative.
        body = self.max_body_chars if self.profile == "large_question" else 2500
        if self.records * (body + 2000) * 14 * self.iterations > self.max_disk_mb * 1024**2:
            raise ValueError(
                "estimated suite disk exceeds --max-disk-mb; reduce corpus or iterations"
            )
        if self.profile == "large_question" and self.max_body_chars < 4096:
            raise ValueError("large_question requires max_body_chars >= 4096")

    @property
    def unique_records(self) -> int:
        return self.records - int(self.records * self.duplicate_rate)


def estimated_peak_bytes(config: Config) -> int:
    """In-memory estimate for one scenario: pages, objects and serialized copies."""
    body = config.max_body_chars if config.profile == "large_question" else 2500
    return (body + 4096) * config.records * 6 + 8 * 1024**2


def host_reserve_bytes(config: Config) -> int:
    """512 MiB for operational runs. The explicit functional probe keeps 32 MiB."""
    functional = (
        config.records <= FUNCTIONAL_MAX_RECORDS
        and config.max_peak_mb <= FUNCTIONAL_MAX_PEAK_MB
        and config.iterations == 1
        and config.profile != "large_question"
    )
    return FUNCTIONAL_RESERVE_BYTES if functional else OPERATIONAL_RESERVE_BYTES


def required_available_bytes(config: Config) -> int:
    estimate = estimated_peak_bytes(config)
    budget = config.max_peak_mb * 1024**2
    if estimate > budget:
        raise ValueError("estimated peak exceeds --max-peak-mb")
    return budget + host_reserve_bytes(config)


def admit_memory(available: int | None, config: Config) -> None:
    if available is None:
        return
    if type(available) is not int or available < 0:
        raise ValueError("available memory is not a usable measurement")
    if available < required_available_bytes(config):
        raise ValueError("insufficient available memory including host reserve")
