"""Ties a provider to the playlist writer.

Kept separate from ``m3u_writer`` so the writer stays a pure function of channels,
and separate from the UI so ``tools/export_cli.py`` can run the identical code path
without Kodi.
"""
from __future__ import annotations

import contextlib
import errno
import os
import time
from typing import Callable, Optional

from ..config import KIND_XTREAM, ProviderConfig
from ..errors import ConnectionLimitError
from ..models import ExportResult
from .m3u_writer import write_m3u

ProgressFn = Callable[[int, int, str], None]

STATE_FILE = "export-state.json"
LOCK_FILE = "export.lock"


@contextlib.contextmanager
def export_lock(directory: str, blocking: bool = False):
    """Prevent two exports from sweeping the provider at the same time.

    A threading lock is not enough: ``addon.py`` and ``service.py`` are separate
    interpreters, and a user pressing "Rebuild now" while the scheduled export runs
    would put two full category sweeps against an account that permits one
    connection. An advisory file lock is the only thing both processes can see.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, LOCK_FILE)
    handle = open(path, "a+")
    try:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows builds of Kodi
            yield
            return
        flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(handle.fileno(), flags)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise ConnectionLimitError(
                    "Another channel list rebuild is already running."
                ) from exc
            raise
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def export_channels(
    provider,
    config: ProviderConfig,
    out_path: str,
    *,
    progress: Optional[ProgressFn] = None,
    renumber: bool = False,
) -> ExportResult:
    """Fetch the live channel list and write it as an M3U for IPTV Simple."""
    if config.kind == KIND_XTREAM:
        url_for = lambda channel: provider.live_url(channel.id)  # noqa: E731
    else:
        url_for = lambda channel: channel.direct_source  # noqa: E731

    directory = os.path.dirname(os.path.abspath(out_path))
    with export_lock(directory):
        channels = provider.iter_channels(progress=progress)
        result = write_m3u(
            out_path,
            channels,
            url_for,
            user_agent=config.user_agent,
            referer=config.referer,
            renumber=renumber,
        )
        _write_state(directory, config.id, result)
    return result


def _write_state(directory: str, provider_id: str, result: ExportResult) -> None:
    """Record when the last export ran, so the service can decide about staleness."""
    import json
    path = os.path.join(directory, STATE_FILE)
    state = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state[provider_id] = {
        "at": int(time.time()),
        "channels": result.channel_count,
        "groups": result.group_count,
        "path": result.path,
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
    except OSError:
        pass


def last_export_time(directory: str, provider_id: str) -> int:
    import json
    try:
        with open(os.path.join(directory, STATE_FILE), "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return int(state.get(provider_id, {}).get("at", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def is_stale(directory: str, provider_id: str, max_age_seconds: int) -> bool:
    last = last_export_time(directory, provider_id)
    if not last:
        return True
    return (time.time() - last) >= max_age_seconds
