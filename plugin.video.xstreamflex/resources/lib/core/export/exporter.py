"""Ties a provider to the playlist writer.

Kept separate from ``m3u_writer`` so the writer stays a pure function of channels,
and separate from the UI so ``tools/export_cli.py`` can run the identical code path
without Kodi.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

from ..config import KIND_XTREAM, ProviderConfig
from ..models import ExportResult
from .m3u_writer import write_m3u

ProgressFn = Callable[[int, int, str], None]

STATE_FILE = "export-state.json"


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

    channels = provider.iter_channels(progress=progress)
    result = write_m3u(
        out_path,
        channels,
        url_for,
        user_agent=config.user_agent,
        referer=config.referer,
        renumber=renumber,
    )
    _write_state(os.path.dirname(out_path), config.id, result)
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
