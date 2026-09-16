"""Execution-scoped secret loading boundary."""

from __future__ import annotations

from collections.abc import Callable


class ExecutionSecret:
    def __init__(self, loader: Callable[[], str]) -> None:
        self._loader = loader
        self._value: str | None = None

    def get(self) -> str:
        if self._value is None:
            self._value = self._loader()
        return self._value
