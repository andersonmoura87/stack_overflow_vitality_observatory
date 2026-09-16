"""Configuration is validated before constructing the network boundary."""

import json
from dataclasses import asdict

import pytest

from stackoverflow_vitality import cli
from stackoverflow_vitality.config import load_config
from stackoverflow_vitality.domain.models import IngestionRun

BASE = 'tag: python\nwindow_start: "2026-09-01T00:00:00Z"\nwindow_end: "2026-09-02T00:00:00Z"\n'


def config_file(tmp_path, extra=""):
    path = tmp_path / "collection.yml"
    path.write_text(BASE + extra, encoding="utf-8")
    return path


def test_yaml_env_cli_precedence_and_secret_is_excluded(tmp_path):
    path = config_file(tmp_path, "site: serverfault\nmax_pages: 2\nretry:\n  max_attempts: 7\n")
    env = {"STACKEXCHANGE_SITE": "superuser", "STACKEXCHANGE_API_KEY": "private-secret"}
    loaded = load_config(path, env, {})
    assert loaded.site == "superuser"
    assert loaded.max_pages == 2
    assert loaded.max_attempts == 7
    loaded = load_config(path, env, {"site": "stackoverflow", "max_pages": 5})
    assert loaded.site == "stackoverflow"
    assert loaded.max_pages == 5
    assert "private-secret" not in json.dumps(asdict(loaded))


@pytest.mark.parametrize(
    "extra",
    [
        "unknown: value\n",
        "api_key: private-secret\n",
        "max_pages: false\n",
        "page_size: 0\n",
        "max_pages: 'two'\n",
        "connect_timeout: 0\n",
        "read_timeout: .nan\n",
        "retry:\n  jitter: 2\n",
        "retry:\n  max_attempts: 0\n",
        "retry:\n  initial_backoff_seconds: -1\n",
        "retry:\n  mystery: 2\n",
        "backfill: 'false'\n",
        "overlap_seconds: -1\n",
        "user_agent: ''\n",
        "tags: [python;java]\n",
        "tags: [false]\n",
    ],
)
def test_invalid_config_fails_before_network(tmp_path, extra):
    with pytest.raises(ValueError):
        load_config(config_file(tmp_path, extra), {}, {})


@pytest.mark.parametrize("content", ["[invalid", "- list", "", "!!python/object:evil {}"])
def test_invalid_yaml_or_mapping_is_rejected(tmp_path, content):
    path = tmp_path / "bad.yml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path, {}, {})


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config(tmp_path / "absent.yml", {}, {})


def test_file_output_and_bad_tag_rejected(tmp_path):
    path = config_file(tmp_path)
    with pytest.raises(ValueError):
        load_config(path, {}, {"output": str(path)})
    with pytest.raises(ValueError):
        load_config(path, {}, {"tag": "python;java"})


def test_dry_run_does_not_construct_client_or_sleep(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run touched a runtime boundary")

    monkeypatch.setattr(cli, "HttpxStackExchangeClient", forbidden)
    monkeypatch.setattr(cli.time, "sleep", forbidden)
    monkeypatch.setenv("STACKEXCHANGE_API_KEY", "private-secret")
    path = config_file(tmp_path, "max_pages: 7\n")
    assert cli.main(["collect-questions", "--config", str(path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["max_pages"] == 7
    assert "private-secret" not in output


@pytest.mark.parametrize(
    "status, code", [("completed", 0), ("empty", 0), ("failed", 1), ("truncated", 1)]
)
def test_cli_connects_config_and_real_sleeper(tmp_path, monkeypatch, status, code):
    captured = {}
    actual_sleep = cli.time.sleep

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            captured["closed"] = True

    def collect(request, run_id, http, bronze, watermarks, *, sleeper):
        assert sleeper is actual_sleep
        captured["request"] = request
        return IngestionRun(run_id, request, request.window.start, status=status)

    monkeypatch.delenv("STACKEXCHANGE_API_KEY", raising=False)
    monkeypatch.setattr(cli, "HttpxStackExchangeClient", Client)
    monkeypatch.setattr(cli, "collect_questions", collect)
    path = config_file(tmp_path, "max_pages: 7\nconnect_timeout: 3\nretry:\n  max_attempts: 2\n")
    assert cli.main(["collect-questions", "--config", str(path)]) == code
    assert captured["sleeper"] is actual_sleep
    assert captured["connect_timeout"] == 3
    assert captured["max_attempts"] == 2
    assert captured["api_key"] is None
    assert captured["request"].max_pages == 7
    assert captured["closed"]
