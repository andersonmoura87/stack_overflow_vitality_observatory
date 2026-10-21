"""Failures that belong to a run manifest rather than the generation key."""


class DumpAbort(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
