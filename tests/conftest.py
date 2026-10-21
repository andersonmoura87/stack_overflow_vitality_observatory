"""Tests cannot use the real network or inherit collection environment settings."""

import socket

import pytest


@pytest.fixture(autouse=True)
def offline_boundaries(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real network access is forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in (
        "STACKEXCHANGE_SITE",
        "STACKEXCHANGE_TAG",
        "STACKEXCHANGE_USER_AGENT",
        "STACKEXCHANGE_OUTPUT",
        "STACKEXCHANGE_API_KEY",
        "STACKEXCHANGE_DUMP_SITE",
        "STACKEXCHANGE_DUMP_PUBLICATION_ID",
        "STACKEXCHANGE_DUMP_CUTOFF",
        "STACKEXCHANGE_DUMP_BRONZE",
        "STACKEXCHANGE_DUMP_QUARANTINE",
        "STACKEXCHANGE_DUMP_REVISION",
        "STACKEXCHANGE_DUMP_INPUT_ROOT",
        "STACKEXCHANGE_DUMP_RECORD_RELATIVE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
