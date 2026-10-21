"""CLI for offline Data Dump ingest."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from stackoverflow_vitality.data_dump.config import DumpIngestConfig, load_dump_config
from stackoverflow_vitality.data_dump.ingest import ingest_data_dump, load_selection
from stackoverflow_vitality.data_dump.source import DumpSourceError
from stackoverflow_vitality.storage.files import LocalLockConflict

_EXCLUDED = {"command", "config", "dry_run", "source", "question_ids_file", "record_relative_path"}
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 3


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ingest = subparsers.add_parser("ingest-data-dump")
    ingest.add_argument("--config", type=Path, default=Path("configs/data_dump.yml"))
    ingest.add_argument("--source", type=Path, required=True)
    ingest.add_argument("--question-ids-file", type=Path, required=True)
    ingest.add_argument("--dry-run", action="store_true")
    ingest.add_argument("--record-relative-path", action="store_true")
    for name, default in asdict(DumpIngestConfig()).items():
        if name == "record_relative_path":
            continue
        option = name.replace("_", "-")
        ingest.add_argument(f"--{option}", dest=name, type=_cli_type(default), default=None)


def main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if "\x00" in str(args.source) or "\x00" in str(args.question_ids_file):
        parser.error("path contains a null byte")
    overrides = {
        key: value
        for key, value in vars(args).items()
        if key not in _EXCLUDED and value is not None
    }
    if args.record_relative_path:
        overrides["record_relative_path"] = True
    if not args.dry_run:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        config = load_dump_config(args.config, _environ(), overrides)
        question_ids = load_selection(args.question_ids_file)
        result = ingest_data_dump(
            config,
            args.source,
            question_ids,
            run_id=str(uuid4()),
            dry_run=args.dry_run,
        )
    except DumpSourceError as error:
        _write(
            {
                "status": "failed",
                "failure_code": error.code,
                "readable": False,
                "consumable": False,
            }
        )
        return EXIT_FAILED
    except LocalLockConflict:
        _write(
            {
                "status": "failed",
                "failure_code": "lock_conflict",
                "readable": False,
                "consumable": False,
            }
        )
        return EXIT_FAILED
    except (ValueError, TypeError, OSError):
        parser.error("invalid data dump configuration, selection or source")
    _write(result)
    if args.dry_run:
        sys.stdout.write("source_identity_status=pending_source_checksum\n")
        sys.stdout.write("ingestion_identity_status=pending_source_checksum\n")
    if result["status"] == "partial":
        return EXIT_PARTIAL
    if result["status"] in {"completed", "empty", "dry_run"}:
        return EXIT_OK
    return EXIT_FAILED


def _environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def _cli_type(default: object) -> type:
    if isinstance(default, bool):
        return bool
    if isinstance(default, int):
        return int
    return str


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
