"""Regressions for immutable publication, corruption and local CAS."""

import json
from dataclasses import replace
from datetime import timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from stackoverflow_vitality.domain.models import CollectionWindow, RunContext, Watermark
from stackoverflow_vitality.storage import files
from stackoverflow_vitality.storage.bronze import (
    LocalBronzeStore,
    LocalWatermarkStore,
    WatermarkConflict,
    run_directory,
)
from stackoverflow_vitality.storage.files import (
    ImmutableConflict,
    IntegrityError,
    LocalLockConflict,
)
from tests.unit.test_bronze import page


def test_identical_replay_does_not_rewrite_and_conflict_preserves_original(tmp_path):
    store = LocalBronzeStore(tmp_path)
    snapshot = page()
    store.write_page(snapshot)
    path = store.page_path(snapshot)
    original = path.read_bytes()
    modified = path.stat().st_mtime_ns
    store.write_page(snapshot)
    assert path.stat().st_mtime_ns == modified
    with pytest.raises(ImmutableConflict):
        store.write_page(replace(snapshot, collector_version="different"))
    assert path.read_bytes() == original
    assert store.confirm_page(snapshot)


@pytest.mark.parametrize("content", [b"{", b"not-json", b"{}", b"[]"])
def test_confirmation_rejects_corrupt_or_truncated_json(tmp_path, content):
    store = LocalBronzeStore(tmp_path)
    snapshot = page()
    store.write_page(snapshot)
    store.page_path(snapshot).write_bytes(content)
    assert not store.confirm_page(snapshot)


def test_invalid_payload_checksum_is_never_published(tmp_path):
    store = LocalBronzeStore(tmp_path)
    snapshot = replace(page(), checksum="invalid")
    with pytest.raises(IntegrityError):
        store.write_page(snapshot)
    assert not store.confirm_page(snapshot)
    assert not list(tmp_path.rglob("page=*.json"))


def test_envelope_metadata_and_size_are_verified(tmp_path):
    store = LocalBronzeStore(tmp_path)
    snapshot = page()
    store.write_page(snapshot)
    path = store.page_path(snapshot)
    record = json.loads(path.read_bytes())
    record["site"] = "another-site"
    path.write_bytes(files.canonical(record))
    assert not store.confirm_page(snapshot)


def test_failed_publication_cleans_temporary_and_confirms_nothing(tmp_path, monkeypatch):
    store = LocalBronzeStore(tmp_path)
    snapshot = page()

    def fail(*args):
        assert not store.confirm_page(snapshot)
        raise OSError("simulated publication failure")

    monkeypatch.setattr(files.os, "link", fail)
    with pytest.raises(OSError):
        store.write_page(snapshot)
    assert not store.confirm_page(snapshot)
    assert not list(tmp_path.rglob(".*.json.*"))


@pytest.mark.parametrize("same", [True, False])
def test_competing_publication_is_atomic_and_never_overwrites(tmp_path, monkeypatch, same):
    store = LocalBronzeStore(tmp_path)
    snapshot = page()
    competing = None

    def race(source, destination):
        nonlocal competing
        competing = Path(source).read_bytes() if same else b'{"winner":"other"}'
        Path(destination).write_bytes(competing)
        raise FileExistsError

    monkeypatch.setattr(files.os, "link", race)
    if same:
        store.write_page(snapshot)
        assert store.confirm_page(snapshot)
    else:
        with pytest.raises(ImmutableConflict):
            store.write_page(snapshot)
    assert store.page_path(snapshot).read_bytes() == competing


def test_run_context_uses_window_utc_and_collision_free_tag_encoding(tmp_path):
    snapshot = page()
    context = RunContext(snapshot.run_id, snapshot.site, "c++", snapshot.window)
    path = run_directory(tmp_path, context)
    assert "tag=c%2B%2B" in str(path)
    assert path != run_directory(tmp_path, replace(context, tag="c##"))
    assert path != run_directory(tmp_path, replace(context, site="other"))
    assert path != run_directory(tmp_path, replace(context, run_id=uuid4()))
    offset = timezone(timedelta(hours=-3))
    window = CollectionWindow(
        snapshot.window.start.astimezone(offset),
        snapshot.window.end.astimezone(offset),
    )
    assert path == run_directory(tmp_path, replace(context, window=window))
    store = LocalBronzeStore(tmp_path)
    assert store.page_path(snapshot) == store.page_path(
        replace(snapshot, recovered_at=snapshot.recovered_at + timedelta(days=10))
    )


def mark(**kwargs):
    snapshot = page()
    return replace(
        Watermark("stackexchange_api", "python", snapshot.window.end, snapshot.recovered_at),
        **kwargs,
    )


def test_watermark_sites_tags_endpoints_and_sources_are_independent(tmp_path):
    store = LocalWatermarkStore(tmp_path / "watermarks.json")
    marks = [
        mark(),
        mark(site="other"),
        mark(tag_or_group="java"),
        mark(endpoint="https://example.test/questions"),
        mark(source="other"),
    ]
    for value in marks:
        store.update(None, value)
    for value in marks:
        assert (
            store.read(value.source, value.tag_or_group, site=value.site, endpoint=value.endpoint)
            == value
        )


def test_monotonic_compare_and_set_rejects_stale_writer(tmp_path):
    store = LocalWatermarkStore(tmp_path / "watermarks.json")
    first = mark()
    store.update(None, first)
    second = replace(first, value=first.value + timedelta(days=1))
    store.update(first, second)
    with pytest.raises(WatermarkConflict):
        store.update(first, replace(second, value=second.value + timedelta(days=1)))
    with pytest.raises(WatermarkConflict):
        store.update(second, first)
    assert store.read(first.source, first.tag_or_group) == second


@pytest.mark.parametrize("corrupt", [b"{", b"[]", b'{"legacy|python":{}}'])
def test_corrupt_watermark_fails_explicitly_and_is_preserved(tmp_path, corrupt):
    path = tmp_path / "watermarks.json"
    path.write_bytes(corrupt)
    store = LocalWatermarkStore(path)
    with pytest.raises(IntegrityError):
        store.read("stackexchange_api", "python")
    with pytest.raises(IntegrityError):
        store.update(None, mark())
    assert path.read_bytes() == corrupt


def test_failed_mutable_publication_preserves_previous_and_releases_lock(tmp_path, monkeypatch):
    path = tmp_path / "watermarks.json"
    store = LocalWatermarkStore(path)
    first = mark()
    store.update(None, first)
    original = path.read_bytes()

    def fail(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(files.os, "replace", fail)
    with pytest.raises(OSError):
        store.update(first, replace(first, value=first.value + timedelta(days=1)))
    assert path.read_bytes() == original
    assert not path.with_name(path.name + ".lock").exists()


def test_second_writer_cannot_enter_local_lock(tmp_path):
    path = tmp_path / "watermarks.json"
    store = LocalWatermarkStore(path)
    with files.exclusive_lock(path.with_name(path.name + ".lock")):
        with pytest.raises(LocalLockConflict):
            store.update(None, mark())
    assert not path.exists()
    store.update(None, mark())


def test_sync_failure_never_publishes_page(tmp_path, monkeypatch):
    store = LocalBronzeStore(tmp_path)
    snapshot = page()

    def fail(*args):
        raise OSError("fsync failure")

    monkeypatch.setattr(files.os, "fsync", fail)
    with pytest.raises(OSError):
        store.write_page(snapshot)
    assert not store.confirm_page(snapshot)
    assert not list(tmp_path.rglob(".*.json.*"))
