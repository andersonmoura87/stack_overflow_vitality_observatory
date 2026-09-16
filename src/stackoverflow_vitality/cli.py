"""Controlled local CLI for incremental question collection."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from stackoverflow_vitality.config import CollectionConfig, load_config
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.ingestion.collector import collect_questions
from stackoverflow_vitality.ingestion.http import HttpxStackExchangeClient
from stackoverflow_vitality.storage.bronze import LocalBronzeStore, LocalWatermarkStore


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    overrides = {
        key: value
        for key, value in vars(args).items()
        if key not in {"command", "config", "dry_run"}
    }
    try:
        config = load_config(args.config, os.environ, overrides)
        request = config.request()
    except (ValueError, TypeError):
        parser.error("invalid configuration; check YAML keys/types, tag, window and output")
    run_id = uuid4()
    if args.dry_run:
        record = asdict(config)
        record.update(run_id=str(run_id), status="dry_run")
        sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    client = HttpxStackExchangeClient(
        user_agent=config.user_agent,
        api_key=os.getenv("STACKEXCHANGE_API_KEY"),
        connect_timeout=config.connect_timeout,
        read_timeout=config.read_timeout,
        max_attempts=config.max_attempts,
        initial_backoff_seconds=config.initial_backoff_seconds,
        max_backoff_seconds=config.max_backoff_seconds,
        sleeper=time.sleep,
        jitter=lambda base: random.uniform(0, base * config.jitter),
    )
    try:
        run = collect_questions(
            request,
            run_id,
            client,
            LocalBronzeStore(config.output),
            LocalWatermarkStore(Path(config.output) / "watermarks.json"),
            sleeper=time.sleep,
        )
    finally:
        client.close()
    sys.stdout.write(json.dumps(to_record(run), sort_keys=True) + "\n")
    return 0 if run.status in {"completed", "empty"} else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stackoverflow_vitality")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect-questions")
    collect.add_argument("--config", type=Path, default=Path("configs/collection.yml"))
    for name, default in asdict(CollectionConfig()).items():
        option = {"window_start": "from", "window_end": "to"}.get(name, name.replace("_", "-"))
        if isinstance(default, bool):
            collect.add_argument(
                f"--{option}",
                dest=name,
                action=argparse.BooleanOptionalAction,
                default=None,
            )
        else:
            collect.add_argument(f"--{option}", dest=name, type=type(default), default=None)
    collect.add_argument("--dry-run", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
