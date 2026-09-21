"""Filesystem adapters; a successful immutable manifest is the only commit point."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from stackoverflow_vitality.domain.models import BronzePage
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.silver.history import empty_state, rebuild
from stackoverflow_vitality.silver.models import (
    SCHEMA_VERSION,
    InputPage,
    TransformationManifest,
    content_hash,
    digest,
    validate_history,
)
from stackoverflow_vitality.storage.files import (
    ImmutableConflict,
    IntegrityError,
    canonical,
    exclusive_lock,
)


def json_lines(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical(record) + b"\n" for record in records)


def write_immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ImmutableConflict("Silver artifact differs")
        return
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ImmutableConflict("concurrent artifact differs") from None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checksums(paths: dict[str, str]) -> dict[str, str]:
    return {name: digest(Path(path).read_bytes()) for name, path in sorted(paths.items())}


def confirm(paths: dict[str, str], expected: dict[str, str]) -> None:
    if checksums(paths) != expected:
        raise IntegrityError("transformation output checksum mismatch")
    for path in paths.values():
        for line in Path(path).read_bytes().splitlines():
            if not isinstance(json.loads(line), dict):
                raise IntegrityError("output record must be a JSON object")


class LocalBronzeReader:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def pages(self, source_run_id: str | None = None) -> Iterator[InputPage]:
        if not self.root.is_dir():
            raise ValueError("Bronze directory does not exist")
        selected = 0
        for path in sorted(self.root.rglob("page=*.json")):
            if not re.fullmatch(r"page=\d+\.json", path.name):
                continue
            if source_run_id and f"run_id={source_run_id}" not in path.parts:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise IntegrityError("Bronze path escapes root")
            data = path.read_bytes()
            try:
                envelope = json.loads(data)
                if not isinstance(envelope, dict):
                    raise ValueError
                if envelope.get("schema_version") not in {
                    "bronze_stackexchange_questions_snapshot.v1",
                    "bronze_stackexchange_questions_snapshot.v2",
                }:
                    raise ValueError
                if source_run_id and envelope.get("run_id") not in {None, source_run_id}:
                    raise ValueError
                payload = envelope.get("payload")
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    raise ValueError
                supplied = envelope.get("checksum")
                if supplied is not None and supplied != BronzePage.payload_checksum(payload):
                    raise ValueError
                # Reject non-finite JSON and guarantee the canonical payload is representable.
                canonical(payload)
            except (ValueError, TypeError):
                raise IntegrityError("unreadable Bronze envelope, schema or checksum") from None
            selected += 1
            yield InputPage(
                path.relative_to(self.root).as_posix(),
                digest(data),
                envelope,
                tuple(payload["items"]),
            )
        if source_run_id and selected == 0:
            raise FileNotFoundError("no Bronze pages for selected source run")


class LocalSilverStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.dataset_id = digest(str(self.root).encode())

    def lock(self) -> AbstractContextManager[None]:
        return exclusive_lock(self.root / ".transform.lock")

    def load(self, manifest: dict[str, Any] | None) -> dict[str, Any]:
        if manifest is None:
            return empty_state()
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise IntegrityError("unsupported committed Silver schema")
        paths = manifest["output_paths"]
        expected = manifest["output_checksums"]
        confirm(paths, expected)
        state = empty_state()
        for name in ("catalog", "observations", "questions"):
            path = Path(paths[name])
            if not path.resolve().is_relative_to(self.root):
                raise IntegrityError("committed Silver path is outside dataset")
            records = [json.loads(line) for line in path.read_bytes().splitlines()]
            if name == "catalog":
                for record in records:
                    key = record.pop("record_hash")
                    if content_hash(record) != key or key in state[name]:
                        raise IntegrityError("invalid content catalog")
                    state[name][key] = record
            elif name == "observations":
                for record in records:
                    key = record["observation_id"]
                    if key in state[name]:
                        raise IntegrityError("duplicate observation")
                    state[name][key] = record
            else:
                state[name] = records
        validate_history(state["questions"])
        original = state["questions"]
        rebuild(state)
        if state["questions"] != original:
            raise IntegrityError("materialization does not match observations")
        return state

    def write(self, state: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
        validate_history(state["questions"])
        catalog = [
            dict(record_hash=key, **state["catalog"][key]) for key in sorted(state["catalog"])
        ]
        observations = [state["observations"][key] for key in sorted(state["observations"])]
        data = {
            "catalog": json_lines(catalog),
            "observations": json_lines(observations),
            "questions": json_lines(state["questions"]),
        }
        generation = digest(canonical({name: digest(value) for name, value in data.items()}))
        paths = {}
        for name, value in data.items():
            path = self.root / "questions" / f"generation={generation}" / f"{name}.jsonl"
            write_immutable(path, value)
            if path.read_bytes() != value:
                raise IntegrityError("Silver write confirmation failed")
            paths[name] = str(path)
        return paths, {name: digest(value) for name, value in data.items()}

    def confirm(self, paths: dict[str, str], expected: dict[str, str]) -> None:
        confirm(paths, expected)


class LocalQuarantineStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def write(
        self,
        run_id: str,
        records: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, str], dict[str, str]]:
        UUID(run_id)
        directory = self.root / "questions" / f"transformation_run_id={run_id}"
        paths = {}
        expected = {}
        for name, rows in (("quarantined_records", records), ("quality_events", events)):
            path = directory / f"{name}.jsonl"
            data = json_lines(rows)
            write_immutable(path, data)
            if path.read_bytes() != data:
                raise IntegrityError("quarantine write confirmation failed")
            paths[name] = str(path)
            expected[name] = digest(data)
        return paths, expected

    def confirm(self, paths: dict[str, str], expected: dict[str, str]) -> None:
        confirm(paths, expected)


class LocalTransformationManifestStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def latest(self, dataset_id: str) -> dict[str, Any] | None:
        manifests: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            record = json.loads(path.read_bytes())
            if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
                raise IntegrityError("unreadable transformation manifest")
            if record.get("dataset_id") == dataset_id and record.get("status") in {
                "completed",
                "empty",
            }:
                if type(record.get("commit_sequence")) is not int:
                    raise IntegrityError("invalid commit sequence")
                manifests.append(record)
        if not manifests:
            return None
        sequences = [item["commit_sequence"] for item in manifests]
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise IntegrityError("ambiguous or incomplete commit history")
        return max(manifests, key=lambda item: cast(int, item["commit_sequence"]))

    def write(self, manifest: TransformationManifest) -> str:
        UUID(manifest.transformation_run_id)
        path = self.root / f"{manifest.transformation_run_id}.json"
        data = canonical(to_record(manifest))
        write_immutable(path, data)
        if path.read_bytes() != data:
            raise IntegrityError("transformation manifest confirmation failed")
        return str(path)


def validate_paths(bronze: Path, silver: Path, quarantine: Path) -> None:
    roots = [path.resolve() for path in (bronze, silver, quarantine)]
    if not roots[0].is_dir():
        raise ValueError("Bronze path must be an existing directory")
    for i, root in enumerate(roots):
        if any(path.exists() and not path.is_dir() for path in (root, *root.parents)):
            raise ValueError("all paths and ancestors must be directories")
        for other in roots[i + 1 :]:
            if root.is_relative_to(other) or other.is_relative_to(root):
                raise ValueError("Bronze, Silver and quarantine roots must not overlap")
