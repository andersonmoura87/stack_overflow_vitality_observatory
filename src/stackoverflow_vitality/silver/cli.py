"""Network-free CLI boundary for Silver transformations."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from uuid import UUID

from stackoverflow_vitality.silver.quality import QualityConfig
from stackoverflow_vitality.silver.storage import (
    LocalBronzeReader,
    LocalQuarantineStore,
    LocalSilverStore,
    LocalTransformationManifestStore,
    validate_paths,
)
from stackoverflow_vitality.silver.transform import transform_questions


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("transform-questions")
    parser.add_argument("--bronze-path", type=Path, required=True)
    parser.add_argument("--silver-path", type=Path, default=Path("data/silver"))
    parser.add_argument("--quarantine-path", type=Path, default=Path("data/quarantine"))
    parser.add_argument("--run-id", type=UUID, help="Select a source Bronze run")
    parser.add_argument("--max-body-chars", type=int, default=500_000)
    parser.add_argument("--dry-run", action="store_true")


def main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        validate_paths(args.bronze_path, args.silver_path, args.quarantine_path)
        config = QualityConfig(max_body_chars=args.max_body_chars)
    except ValueError as error:
        parser.error(str(error))
    reader = LocalBronzeReader(args.bronze_path)
    source_id = str(args.run_id) if args.run_id else None
    if args.dry_run:
        try:
            pages = list(reader.pages(source_id))
        except (ValueError, OSError, RuntimeError):
            sys.stdout.write(json.dumps({"status": "failed", "input_pages": 0}) + "\n")
            return 1
        sys.stdout.write(
            json.dumps(
                {
                    "status": "dry_run",
                    "input_pages": len(pages),
                    "input_records": sum(len(page.items) for page in pages),
                    "silver_path": str(args.silver_path),
                    "quarantine_path": str(args.quarantine_path),
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 0
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    manifest = transform_questions(
        reader,
        LocalSilverStore(args.silver_path),
        LocalQuarantineStore(args.quarantine_path),
        LocalTransformationManifestStore(args.silver_path / "manifests"),
        source_run_id=source_id,
        config=config,
    )
    sys.stdout.write(
        json.dumps(
            {
                "status": manifest.status,
                "input_records": manifest.input_records,
                "accepted_records": manifest.accepted_records,
                "quarantined_records": manifest.quarantined_records,
                "unchanged_records": manifest.unchanged_records,
                "new_versions": manifest.new_versions,
                "closed_versions": manifest.closed_versions,
                "output_paths": manifest.output_paths,
                "manifest_path": str(
                    args.silver_path / "manifests" / f"{manifest.transformation_run_id}.json"
                ),
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if manifest.status in {"completed", "empty"} else 1
