"""CLI for the offline Data Dump Silver transformation."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from stackoverflow_vitality.data_dump_silver.transform import transform_data_dump

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 3
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("transform-data-dump")
    parser.add_argument("--bronze-generation", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--transformation-run-id", required=True)
    parser.add_argument("--max-body-chars", type=int, default=500_000)
    parser.add_argument("--dry-run", action="store_true")


def main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    _reject_nulls(args, parser)
    if args.max_body_chars < 1 or _RUN_ID.fullmatch(args.transformation_run_id) is None:
        parser.error("transformation arguments are invalid")
    if not args.dry_run:
        logging.basicConfig(
            level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s"
        )
    result = transform_data_dump(
        args.bronze_generation,
        args.output_dir,
        transformation_run_id=args.transformation_run_id,
        max_body_chars=args.max_body_chars,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result.get("dry_run") is True and result.get("status") == "dry_run":
        return EXIT_OK
    status = result.get("planned_status", result.get("status"))
    if status in {"completed", "empty"}:
        return EXIT_OK
    if status == "partial":
        return EXIT_PARTIAL
    return EXIT_FAILED


def _reject_nulls(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    for value in (args.bronze_generation, args.output_dir, args.transformation_run_id):
        if "\x00" in str(value):
            parser.error("paths reject null bytes")
