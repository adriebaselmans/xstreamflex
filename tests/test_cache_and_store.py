import os

import pytest

from core.cache import Cache, cached
from core.config import ProviderConfig, ProviderStore
from core.export.exporter import is_stale, last_export_time


def test_get_returns_none_after_expiry(tmp_path):
    cache = Cache(str(tmp_path / "c.db"))
    cache.set("k", {"v": 1}, ttl=-1)
    assert cache.get("k") is None


def test_stale_value_survives_expiry(tmp_path):
    cache = Cache(str(tmp_path / "c.db"))
    cache.set("k", {"v": 1}, ttl=-1)
    assert cache.get_stale("k") == {"v": 1}


def test_cached_serves_stale_when_producer_fails(tmp_path):
    cache = Cache(str(tmp_path / "c.db"))
    cache.set("k", ["old"], ttl=-1)

    def boom():
        raise RuntimeError("provider down")

    assert cached(cache, "k", 60, boom) == ["old"]


def test_cached_propagates_failure_without_stale_entry(tmp_path):
    cache = Cache(str(tmp_path / "c.db"))

    def boom():
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        cached(cache, "k", 60, boom)


def test_cached_stores_and_reuses(tmp_path):
    cache = Cache(str(tmp_path / "c.db"))
    calls = []

    def produce():
        calls.append(1)
        return {"a": 1}

    assert cached(cache, "k", 60, produce) == {"a": 1}
    assert cached(cache, "k", 60, produce) == {"a": 1}
    assert len(calls) == 1


def test_ttl_multiplier_zero_disables_writes(tmp_path):
    cache = Cache(str(tmp_path / "c.db"), ttl_multiplier=0.0)
    cache.set("k", 1, ttl=60)
    assert cache.get("k") is None


def test_invalidate_by_prefix(tmp_path):
    cache = Cache(str(tmp_path / "c.db"))
    cache.set("xtream:a:one", 1, 60)
    cache.set("xtream:a:two", 2, 60)
    cache.set("xtream:b:one", 3, 60)

    assert cache.invalidate("xtream:a") == 2
    assert cache.get("xtream:b:one") == 3


def test_store_roundtrip_and_active_selection(tmp_path):
    path = str(tmp_path / "providers.json")
    store = ProviderStore(path)
    first = ProviderConfig(label="One", base_url="http://a:8080", username="u", password="p")
    second = ProviderConfig(label="Two", base_url="http://b:8080", username="u", password="p")

    store.upsert(first)
    store.upsert(second)
    assert store.active().id == first.id  # first added becomes active

    store.set_active(second.id)
    assert ProviderStore(path).active().label == "Two"


def test_store_removes_and_repoints_active(tmp_path):
    path = str(tmp_path / "providers.json")
    store = ProviderStore(path)
    first = ProviderConfig(label="One", base_url="http://a", username="u", password="p")
    second = ProviderConfig(label="Two", base_url="http://b", username="u", password="p")
    store.upsert(first)
    store.upsert(second)
    store.set_active(second.id)

    store.remove(second.id)
    assert store.active().id == first.id


def test_store_file_is_not_world_readable(tmp_path):
    import stat
    path = str(tmp_path / "providers.json")
    store = ProviderStore(path)
    store.upsert(ProviderConfig(label="One", base_url="http://a", username="u", password="p"))

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0


def test_store_survives_a_corrupt_file(tmp_path):
    path = str(tmp_path / "providers.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")

    store = ProviderStore(path)
    assert store.all() == []


def test_export_state_tracks_staleness(tmp_path):
    from core.export.exporter import _write_state
    from core.models import ExportResult

    directory = str(tmp_path)
    assert is_stale(directory, "abc", 3600) is True

    _write_state(directory, "abc", ExportResult(path="x", channel_count=5))
    assert last_export_time(directory, "abc") > 0
    assert is_stale(directory, "abc", 3600) is False
    assert is_stale(directory, "abc", 0) is True
    assert is_stale(directory, "other", 3600) is True
