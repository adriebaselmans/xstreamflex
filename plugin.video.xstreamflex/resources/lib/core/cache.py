"""Disk cache.

Kodi starts a fresh interpreter for every ``plugin://`` call, so nothing survives in
memory between user actions. All state that is expensive to fetch lives here.

Stale rows are kept after expiry on purpose: when a provider call fails, serving
yesterday's channel list beats serving an empty one.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

# Seconds. Tuned to how often each kind of data actually changes upstream.
TTL_ACCOUNT = 3600
TTL_CATEGORIES = 86400
TTL_CHANNELS = 21600
TTL_METADATA = 604800
TTL_SHORT_EPG = 900

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at REAL NOT NULL,
    stored_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_expiry ON entries (expires_at);
"""


class Cache:
    def __init__(self, db_path: str, ttl_multiplier: float = 1.0) -> None:
        self.db_path = db_path
        self.ttl_multiplier = max(0.0, ttl_multiplier)
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A short busy timeout matters: addon.py and service.py are separate
        # processes and can write this file at the same moment.
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, key: str, default: Any = None) -> Any:
        row = self._row(key)
        if row is None:
            return default
        value, expires_at = row
        if expires_at < time.time():
            return default
        return value

    def get_stale(self, key: str, default: Any = None) -> Any:
        """Return a value regardless of expiry — the degradation path."""
        row = self._row(key)
        return default if row is None else row[0]

    def _row(self, key: str):
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "SELECT value, expires_at FROM entries WHERE key = ?", (key,)
                )
                found = cur.fetchone()
        except sqlite3.Error:
            return None
        if not found:
            return None
        try:
            return json.loads(found[0]), float(found[1])
        except (ValueError, TypeError):
            return None

    def set(self, key: str, value: Any, ttl: float) -> None:
        if self.ttl_multiplier <= 0:
            return  # caching disabled, used when debugging provider behaviour
        now = time.time()
        payload = json.dumps(value, separators=(",", ":"))
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO entries (key, value, expires_at, stored_at)"
                    " VALUES (?, ?, ?, ?)",
                    (key, payload, now + ttl * self.ttl_multiplier, now),
                )
        except sqlite3.Error:
            pass  # a cache write failure must never break a working request

    def invalidate(self, prefix: str = "") -> int:
        try:
            with self._connect() as conn:
                if prefix:
                    cur = conn.execute(
                        "DELETE FROM entries WHERE key LIKE ?", (prefix + "%",)
                    )
                else:
                    cur = conn.execute("DELETE FROM entries")
                return cur.rowcount or 0
        except sqlite3.Error:
            return 0

    def purge_expired(self) -> int:
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM entries WHERE expires_at < ?", (time.time(),)
                )
                return cur.rowcount or 0
        except sqlite3.Error:
            return 0

    def size_bytes(self) -> int:
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0


class NullCache(Cache):
    """Drop-in used by tools and tests that should always hit the provider."""

    def __init__(self) -> None:  # pylint: disable=super-init-not-called
        self.db_path = ":memory:"
        self.ttl_multiplier = 0.0

    def get(self, key: str, default: Any = None) -> Any:
        return default

    def get_stale(self, key: str, default: Any = None) -> Any:
        return default

    def set(self, key: str, value: Any, ttl: float) -> None:
        return

    def invalidate(self, prefix: str = "") -> int:
        return 0

    def purge_expired(self) -> int:
        return 0

    def size_bytes(self) -> int:
        return 0


def cached(cache: Cache, key: str, ttl: float, producer, logger=None) -> Any:
    """Fetch through the cache, degrading to a stale entry when the producer fails."""
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        value = producer()
    except Exception:
        stale = cache.get_stale(key)
        if stale is not None:
            if logger:
                logger("warning", "using stale cache for %s after provider failure" % key)
            return stale
        raise
    cache.set(key, value, ttl)
    return value
