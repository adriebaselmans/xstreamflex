"""Writes the playlist that IPTV Simple reads.

This file is the whole point of the project: IPTV Simple cannot fetch a channel list
from a provider whose ``get.php`` is disabled, but it is perfectly happy reading a
local file. We produce that file from the API.
"""
from __future__ import annotations

import os
import tempfile
from typing import Callable, Dict, Iterable, Optional

from ..models import Channel, ExportResult

UrlBuilder = Callable[[Channel], str]


def _escape_attr(value: str) -> str:
    """Attribute values are double-quoted, so quotes and newlines must not survive."""
    return (value or "").replace('"', "'").replace("\n", " ").replace("\r", " ").strip()


def _escape_name(value: str) -> str:
    """The display name runs to end of line, so only line breaks and commas hurt."""
    return (value or "").replace("\n", " ").replace("\r", " ").strip()


def write_m3u(
    path: str,
    channels: Iterable[Channel],
    url_for: UrlBuilder,
    *,
    user_agent: str,
    referer: str = "",
    extra_props: Optional[Dict[str, str]] = None,
    renumber: bool = False,
) -> ExportResult:
    """Write ``channels`` to ``path`` atomically.

    Atomic because IPTV Simple may read the file at any moment, including on its own
    refresh timer, and half a playlist is worse than yesterday's playlist.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    result = ExportResult(path=path)
    groups = set()
    seen_ids = set()
    number = 0

    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=directory,
        prefix=".xstreamflex-", suffix=".m3u", delete=False,
    )
    tmp_path = handle.name
    try:
        with handle:
            handle.write("#EXTM3U\n")
            for channel in channels:
                if not channel.id:
                    result.skipped.append("%s: no stream id" % (channel.name or "unnamed"))
                    continue
                url = url_for(channel)
                if not url:
                    result.skipped.append("%s: no stream URL" % channel.name)
                    continue
                if channel.id in seen_ids:
                    # Panels list the same stream in several categories; IPTV Simple
                    # would otherwise show visible duplicates.
                    continue
                seen_ids.add(channel.id)
                number += 1

                group = channel.group or channel.category_id or "Ungrouped"
                groups.add(group)

                chno = number if renumber else (channel.number or number)
                handle.write(
                    '#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-chno="%d" tvg-logo="%s"'
                    ' group-title="%s",%s\n' % (
                        _escape_attr(channel.epg_channel_id or channel.id),
                        _escape_attr(channel.name),
                        chno,
                        _escape_attr(channel.logo),
                        _escape_attr(group),
                        _escape_name(channel.name),
                    )
                )

                # Written for every channel, never conditionally: the reference
                # provider refuses media requests without a User-Agent (454), and a
                # per-channel option cannot be forgotten the way a global setting can.
                channel_ua = channel.headers.get("User-Agent") or user_agent
                if channel_ua:
                    handle.write("#EXTVLCOPT:http-user-agent=%s\n" % channel_ua)
                channel_referer = channel.headers.get("Referer") or referer
                if channel_referer:
                    handle.write("#EXTVLCOPT:http-referrer=%s\n" % channel_referer)

                props = dict(extra_props or {})
                props.update(channel.kodi_props)
                if url.split("?", 1)[0].endswith(".ts"):
                    # Naming the container saves the player a probe round on a live
                    # stream. HLS is left alone; Kodi sniffs manifests reliably.
                    props.setdefault("mimetype", "video/mp2t")
                for key, value in sorted(props.items()):
                    handle.write("#KODIPROP:%s=%s\n" % (key, value))

                handle.write("%s\n" % url)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(tmp_path, path)
        tmp_path = ""
        try:
            # The URLs embed the account password, as every Xtream client's do.
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    result.channel_count = number
    result.group_count = len(groups)
    try:
        result.bytes_written = os.path.getsize(path)
    except OSError:
        result.bytes_written = 0
    return result
