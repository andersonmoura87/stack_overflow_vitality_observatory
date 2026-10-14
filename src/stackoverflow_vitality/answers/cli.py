"""CLI for answer collection and offline transformation."""

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

from stackoverflow_vitality.answers.batching import load_question_ids, plan_batches
from stackoverflow_vitality.answers.bronze import LocalAnswerBronzeStore
from stackoverflow_vitality.answers.checkpoint import LocalAnswerCheckpoint
from stackoverflow_vitality.answers.collect import collect_answers
from stackoverflow_vitality.answers.config import AnswerCollectionConfig, load_answer_config
from stackoverflow_vitality.answers.transform import AnswerTransformFailure, transform_answers
from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.ingestion.http import HttpxStackExchangeClient


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    collect = subparsers.add_parser("collect-answers")
    collect.add_argument("--config", type=Path, default=Path("configs/answers.yml"))
    collect.add_argument("--question-ids-file", type=Path, required=True)
    collect.add_argument("--max-batches", type=int, default=None)
    collect.add_argument("--dry-run", action="store_true")
    for name, default in asdict(AnswerCollectionConfig()).items():
        option = name.replace("_", "-")
        collect.add_argument(f"--{option}", dest=name, type=type(default), default=None)
    transform = subparsers.add_parser("transform-answers")
    transform.add_argument("--bronze-path", type=Path, required=True)
    transform.add_argument("--silver-path", type=Path, required=True)
    transform.add_argument("--quarantine-path", type=Path, required=True)
    transform.add_argument("--site", default=None)
    transform.add_argument("--run-id", default=None)
    transform.add_argument("--questions-silver", type=Path, default=None)
    transform.add_argument("--max-body-chars", type=int, default=500_000)
    transform.add_argument("--dry-run", action="store_true")


def main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command == "transform-answers":
        return _transform(args, parser)
    return _collect(args, parser)


def _collect(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    overrides = {
        key: value
        for key, value in vars(args).items()
        if key
        not in {
            "command",
            "config",
            "dry_run",
            "question_ids_file",
            "max_batches",
            "max_pages",
        }
    }
    try:
        config = load_answer_config(args.config, os.environ, overrides)
        payload = json.loads(args.question_ids_file.read_text(encoding="utf-8"))
        question_ids = load_question_ids(payload)
        batches = plan_batches(
            question_ids,
            endpoint=config.endpoint,
            max_batch_size=config.max_batch_size,
            max_url_length=config.max_url_length,
            query_allowance=config.query_allowance,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        parser.error("invalid answer configuration or question id file")
    if args.dry_run:
        record = asdict(config)
        sys.stdout.write(
            json.dumps(
                {
                    "status": "dry_run",
                    "site": config.site,
                    "endpoint": config.endpoint,
                    "question_ids": len(question_ids),
                    "batches": len(batches),
                    "max_batch_size": config.max_batch_size,
                    "max_pages": args.max_pages or config.max_pages,
                    "max_batches": args.max_batches,
                    "mode": config.mode,
                    "concurrency": config.concurrency,
                    "config": record,
                },
                sort_keys=True,
            )
            + "\n"
        )
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
        result = collect_answers(
            config,
            batches,
            origin=args.question_ids_file.name,
            run_id=uuid4(),
            http=client,
            bronze=LocalAnswerBronzeStore(config.bronze_output),
            checkpoints=LocalAnswerCheckpoint(config.bronze_output),
            mode=config.mode,
            max_batches=args.max_batches,
            max_pages=args.max_pages,
            sleeper=time.sleep,
        )
    finally:
        client.close()
    sys.stdout.write(json.dumps(to_record(result), sort_keys=True) + "\n")
    return 0 if result.status in {"completed", "empty"} else 1


def _transform(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        if args.max_body_chars < 1:
            raise ValueError
        for path in (args.bronze_path, args.silver_path, args.quarantine_path):
            if not path.is_dir():
                raise ValueError
        result = transform_answers(
            args.bronze_path,
            args.silver_path,
            args.quarantine_path,
            site=args.site,
            run_id=args.run_id,
            questions_silver=args.questions_silver,
            dry_run=args.dry_run,
            max_body_chars=args.max_body_chars,
        )
    except AnswerTransformFailure as error:
        parser.exit(1, f"{error}\n")
    except (ValueError, OSError):
        parser.error("invalid transform-answers arguments")
    sys.stdout.write(json.dumps(to_record(result), sort_keys=True) + "\n")
    return 0 if result.status in {"completed", "empty", "partial", "dry_run"} else 1
