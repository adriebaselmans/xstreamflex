"""Plain M3U / M3U8 source.

Parsed line by line rather than loaded whole: provider playlists routinely run to
tens of megabytes, and Kodi add-ons share the host's memory with the player.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Dict, Iterable, Iterator, List, Optional

from ..cache import Cache, NullCache, TTL_CHANNELS
from ..config import ProviderConfig
from ..errors import ParseError
from ..http import HttpClient
from ..models import LIVE, Capabilities, Category, Channel, StreamRef
from .base import BaseProvider

_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
_DURATION_RE = re.compile(r'^-?\d+(?:\.\d+)?')


def split_extinf(line: str):
    """Split ``#EXTINF:<duration> <attrs>,<display name>`` into its three parts.

    Returns ``None`` for a line that is not a usable EXTINF.

    A regex cannot do this: ``tvg-name="BBC, One"`` puts a comma inside an attribute
    value, and the display name may contain commas of its own. The attribute section
    ends at the first comma that is *not* inside double quotes; everything after that
    is the name, commas and all.
    """
    body = line[len("#EXTINF:"):]
    duration = _DURATION_RE.match(body)
    if duration is None:
        return None
    rest = body[duration.end():]

    in_quotes = False
    for index, char in enumerate(rest):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            return duration.group(0), rest[:index], rest[index + 1:].strip()
    return None


class M3UProvider(BaseProvider):
    kind = "m3u"
    capabilities = Capabilities(live=True, vod=False, series=False, short_epg=False,
                                xmltv_url=True)

    def __init__(self, config: ProviderConfig, client: HttpClient,
                 cache: Optional[Cache] = None, logger=None) -> None:
        self.config = config
        self.client = client
        self.cache = cache or NullCache()
        self._log = logger or (lambda level, message: None)

    def _lines(self) -> Iterator[str]:
        source = self.config.m3u_url
        if not source:
            raise ParseError("No playlist URL or path is configured.")
        if "://" not in source or source.startswith("file://"):
            path = source[7:] if source.startswith("file://") else source
            if not os.path.exists(path):
                raise ParseError("Playlist file not found.", path)
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    yield line.rstrip("\r\n")
        else:
            for line in self.client.iter_lines(source):
                yield line.rstrip("\r\n")

    def iter_channels(self, categories=None, progress=None) -> Iterator[Channel]:
        yield from parse_m3u(self._lines(), default_user_agent=self.config.user_agent)

    def channels(self, category_id: Optional[str] = None) -> List[Channel]:
        key = "m3u:%s:channels" % self.config.id
        cached_rows = self.cache.get(key)
        if cached_rows is None:
            channels = list(self.iter_channels())
            self.cache.set(key, [_channel_to_dict(c) for c in channels], TTL_CHANNELS)
        else:
            channels = [_channel_from_dict(row) for row in cached_rows]
        if category_id:
            return [c for c in channels if c.category_id == category_id]
        return channels

    def categories(self, kind: str) -> List[Category]:
        if kind != LIVE:
            return []
        seen: Dict[str, str] = {}
        for channel in self.channels():
            if channel.category_id and channel.category_id not in seen:
                seen[channel.category_id] = channel.group or channel.category_id
        return [Category(id=cid, name=name, kind=LIVE) for cid, name in seen.items()]

    def live_stream(self, channel: Channel) -> StreamRef:
        headers = dict(channel.headers)
        headers.setdefault("User-Agent", self.config.user_agent)
        mime = "video/mp2t" if channel.direct_source.endswith(".ts") else ""
        return StreamRef(
            url=channel.direct_source,
            headers=headers,
            mime_type=mime,
            inputstream=channel.kodi_props.get("inputstream", ""),
            live=True,
        )


def parse_m3u(lines: Iterable[str], default_user_agent: str = "") -> Iterator[Channel]:
    """Turn playlist lines into channels.

    Recognises ``#EXTINF`` attributes, ``#EXTGRP``, ``#EXTVLCOPT`` (converted to HTTP
    headers, matching how IPTV Simple treats them) and ``#KODIPROP`` (passed through
    untouched, since it may carry DRM or inputstream configuration).
    """
    name = ""
    attrs: Dict[str, str] = {}
    headers: Dict[str, str] = {}
    props: Dict[str, str] = {}
    group_override = ""

    def reset():
        return "", {}, {}, {}, ""

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#EXTM3U"):
            continue

        if line.startswith("#EXTINF:"):
            parts = split_extinf(line)
            if parts is None:
                name, attrs, headers, props, group_override = reset()
                continue
            _, attr_text, name = parts
            attrs = {k.lower(): v for k, v in _ATTR_RE.findall(attr_text)}
            headers, props, group_override = {}, {}, ""
            continue

        if line.startswith("#EXTGRP:"):
            group_override = line.split(":", 1)[1].strip()
            continue

        if line.startswith("#EXTVLCOPT:"):
            option = line.split(":", 1)[1].strip()
            key, _, value = option.partition("=")
            key = key.strip().lower()
            if key == "http-user-agent":
                headers["User-Agent"] = value.strip()
            elif key == "http-referrer":
                headers["Referer"] = value.strip()
            elif key == "http-origin":
                headers["Origin"] = value.strip()
            continue

        if line.startswith("#KODIPROP:"):
            option = line.split(":", 1)[1].strip()
            key, _, value = option.partition("=")
            if key:
                props[key.strip()] = value.strip()
            continue

        if line.startswith("#"):
            continue

        # A bare line following an #EXTINF is the stream URL.
        if not name:
            continue
        if default_user_agent:
            headers.setdefault("User-Agent", default_user_agent)
        group = group_override or attrs.get("group-title", "")
        yield Channel(
            id=attrs.get("tvg-id") or _stable_id(line),
            name=attrs.get("tvg-name") or name,
            category_id=group or "ungrouped",
            number=_safe_int(attrs.get("tvg-chno")),
            logo=attrs.get("tvg-logo", ""),
            epg_channel_id=attrs.get("tvg-id", ""),
            direct_source=line,
            headers=dict(headers),
            kodi_props=dict(props),
            group=group or "Ungrouped",
        )
        name, attrs, headers, props, group_override = reset()


def _stable_id(url: str) -> str:
    """Reproducible id for playlists without tvg-id, so exports do not churn."""
    return "m3u" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _safe_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _channel_to_dict(channel: Channel) -> dict:
    return {
        "id": channel.id, "name": channel.name, "category_id": channel.category_id,
        "number": channel.number, "logo": channel.logo,
        "epg_channel_id": channel.epg_channel_id, "direct_source": channel.direct_source,
        "headers": channel.headers, "kodi_props": channel.kodi_props, "group": channel.group,
    }


def _channel_from_dict(row: dict) -> Channel:
    return Channel(
        id=row.get("id", ""), name=row.get("name", ""),
        category_id=row.get("category_id", ""), number=row.get("number", 0),
        logo=row.get("logo", ""), epg_channel_id=row.get("epg_channel_id", ""),
        direct_source=row.get("direct_source", ""), headers=row.get("headers") or {},
        kodi_props=row.get("kodi_props") or {}, group=row.get("group", ""),
    )
