"""Temporary instrumentation; production functions and results are unchanged."""

import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from functools import wraps
from typing import ParamSpec, TypeVar
from unittest.mock import patch

from stackoverflow_vitality.silver import content, history, quality, storage, transform
from stackoverflow_vitality.silver.models import InputPage
from stackoverflow_vitality.silver.ports import BronzeReader

P = ParamSpec("P")
R = TypeVar("R")


class Timings:
    def __init__(self) -> None:
        self.exclusive: dict[str, float] = defaultdict(float)
        self.inclusive: dict[str, float] = defaultdict(float)
        self.stack: list[list[float]] = []

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        frame = [time.perf_counter(), 0.0]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = time.perf_counter() - frame[0]
            self.stack.pop()
            self.inclusive[name] += elapsed
            self.exclusive[name] += elapsed - frame[1]
            if self.stack:
                self.stack[-1][1] += elapsed

    def wrapper(self, function: Callable[P, R], name: str) -> Callable[P, R]:
        @wraps(function)
        def measured(*args: P.args, **kwargs: P.kwargs) -> R:
            with self.span(name):
                return function(*args, **kwargs)

        return measured

    @contextmanager
    def install(self) -> Iterator["Timings"]:
        with ExitStack() as patches:
            points = [
                (transform, "normalize", "quality"),
                (quality, "clean_body", "parse_clean"),
                (quality, "clean_title", "parse_clean"),
                (transform, "observe", "history"),
                (transform, "rebuild", "rebuild"),
                (storage, "rebuild", "rebuild"),
                (storage.LocalSilverStore, "load", "load_existing"),
                (storage.LocalSilverStore, "write", "generation"),
                (storage.LocalQuarantineStore, "write", "quarantine"),
                (storage, "json_lines", "serialization"),
                (storage, "write_immutable", "persistence"),
                (storage, "confirm", "confirmation"),
                (storage.LocalTransformationManifestStore, "write", "manifest"),
                (storage.LocalTransformationManifestStore, "latest", "manifest"),
                (history, "validate_history", "contract"),
                (storage, "validate_history", "contract"),
            ]
            for target, attribute, name in points:
                patches.enter_context(
                    patch.object(target, attribute, self.wrapper(getattr(target, attribute), name))
                )
            # Parsing and extraction share a parser. Attribute only code-handling callbacks,
            # not tokenizer time, to code_extract. This is a lower-bound component, not ablation.
            for method in ("handle_starttag", "handle_endtag", "handle_data", "finish_code"):
                callback = getattr(content.ContentParser, method)
                if not callable(callback):
                    raise TypeError(method)
                original: Callable[..., object] = callback

                def code_callback(
                    parser: content.ContentParser,
                    *args: object,
                    _original: Callable[..., object] = original,
                    _method: str = method,
                ) -> object:
                    code = parser.code is not None or (
                        _method in {"handle_starttag", "handle_endtag"} and args[0] == "code"
                    )
                    if code:
                        with self.span("code_extract"):
                            return _original(parser, *args)
                    return _original(parser, *args)

                patches.enter_context(patch.object(content.ContentParser, method, code_callback))
            yield self


class TimedReader:
    def __init__(self, reader: BronzeReader, timings: Timings) -> None:
        self.reader = reader
        self.timings = timings

    def pages(self, source_run_id: str | None = None) -> Iterator[InputPage]:
        iterator = iter(self.reader.pages(source_run_id))
        while True:
            with self.timings.span("bronze_read"):
                page = next(iterator, None)
            if page is None:
                return
            yield page
