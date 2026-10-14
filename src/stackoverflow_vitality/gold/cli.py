"""Offline CLI for the descriptive Gold vitality build."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.gold.publish import build_vitality
from stackoverflow_vitality.silver.models import utc
from stackoverflow_vitality.storage.files import (
    ImmutableConflict,
    IntegrityError,
    LocalLockConflict,
)


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("build-vitality")
    parser.add_argument("--silver-path", type=Path, required=True)
    parser.add_argument("--gold-path", type=Path, default=Path("data/gold"))
    parser.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help="UTC cutoff ending in Z; required when publishing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the committed Silver generation and print counts without writing",
    )


def main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    explicit = None
    if args.as_of is not None:
        if not isinstance(args.as_of, str) or not args.as_of.endswith("Z"):
            parser.error("as_of must be an ISO-8601 UTC timestamp ending in Z")
        try:
            explicit = utc(args.as_of)
        except ValueError:
            parser.error("as_of must be an ISO-8601 UTC timestamp ending in Z")
    try:
        result = build_vitality(
            args.silver_path,
            args.gold_path,
            explicit,
            dry_run=bool(args.dry_run),
        )
    except (ValueError, IntegrityError, ImmutableConflict, LocalLockConflict) as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(json.dumps(to_record(result), sort_keys=True) + "\n")
    return 0
