"""Separate injectable boundaries for transformation IO."""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, Protocol

from stackoverflow_vitality.silver.models import InputPage, TransformationManifest


class BronzeReader(Protocol):
    def pages(self, source_run_id: str | None = None) -> Iterator[InputPage]: ...


class SilverStore(Protocol):
    dataset_id: str

    def lock(self) -> AbstractContextManager[None]: ...
    def load(self, manifest: dict[str, Any] | None) -> dict[str, Any]: ...
    def write(self, state: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]: ...
    def confirm(self, paths: dict[str, str], checksums: dict[str, str]) -> None: ...


class QuarantineStore(Protocol):
    def write(
        self,
        run_id: str,
        records: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, str]]: ...
    def confirm(self, paths: dict[str, str], checksums: dict[str, str]) -> None: ...


class TransformationManifestStore(Protocol):
    def latest(self, dataset_id: str) -> dict[str, Any] | None: ...
    def write(self, manifest: TransformationManifest) -> str: ...
