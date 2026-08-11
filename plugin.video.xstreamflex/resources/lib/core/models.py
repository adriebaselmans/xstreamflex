"""Data carried between the provider layer and the UI.

Plain dataclasses with no Kodi types, so they can be built and asserted on in tests
that run without Kodi installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LIVE = "live"
VOD = "vod"
SERIES = "series"


def _int(value: Any, default: int = 0) -> int:
    """Xtream panels return numbers as strings, as numbers, or as null."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def _str(value: Any, default: str = "") -> str:
    if value is None or value is False:
        return default
    return str(value).strip()


@dataclass(frozen=True)
class Account:
    auth: bool = False
    status: str = ""
    expires_at: int = 0
    is_trial: bool = False
    max_connections: int = 1
    active_connections: int = 0
    allowed_output_formats: List[str] = field(default_factory=lambda: ["ts"])
    server_url: str = ""
    server_port: str = ""
    https_port: str = ""
    protocol: str = "http"
    timezone: str = ""

    @property
    def is_active(self) -> bool:
        return self.auth and self.status.lower() == "active"

    @property
    def free_connections(self) -> int:
        return max(0, self.max_connections - self.active_connections)

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "Account":
        user = payload.get("user_info") or {}
        server = payload.get("server_info") or {}
        formats = user.get("allowed_output_formats") or ["ts"]
        if isinstance(formats, str):
            formats = [f.strip() for f in formats.split(",") if f.strip()]
        return cls(
            auth=bool(_int(user.get("auth"))),
            status=_str(user.get("status")),
            expires_at=_int(user.get("exp_date")),
            is_trial=bool(_int(user.get("is_trial"))),
            # A panel that omits max_connections is assumed to be the strictest
            # case; over-restricting costs a little speed, under-restricting
            # produces failures that look like broken streams.
            max_connections=max(1, _int(user.get("max_connections"), 1)),
            active_connections=_int(user.get("active_cons")),
            allowed_output_formats=[str(f) for f in formats],
            server_url=_str(server.get("url")),
            server_port=_str(server.get("port")),
            https_port=_str(server.get("https_port")),
            protocol=_str(server.get("server_protocol"), "http"),
            timezone=_str(server.get("timezone")),
        )


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    kind: str = LIVE

    @classmethod
    def from_api(cls, item: Dict[str, Any], kind: str) -> "Category":
        return cls(
            id=_str(item.get("category_id")),
            name=_str(item.get("category_name")) or "Unnamed",
            kind=kind,
        )


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    category_id: str = ""
    number: int = 0
    logo: str = ""
    epg_channel_id: str = ""
    tv_archive: bool = False
    archive_days: int = 0
    direct_source: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    kodi_props: Dict[str, str] = field(default_factory=dict)
    group: str = ""

    @classmethod
    def from_api(cls, item: Dict[str, Any]) -> "Channel":
        return cls(
            id=_str(item.get("stream_id")),
            name=_str(item.get("name")) or "Unnamed",
            category_id=_str(item.get("category_id")),
            number=_int(item.get("num")),
            logo=_str(item.get("stream_icon")),
            epg_channel_id=_str(item.get("epg_channel_id")),
            tv_archive=bool(_int(item.get("tv_archive"))),
            archive_days=_int(item.get("tv_archive_duration")),
            direct_source=_str(item.get("direct_source")),
        )


@dataclass(frozen=True)
class Movie:
    id: str
    name: str
    category_id: str = ""
    icon: str = ""
    container_extension: str = "mp4"
    added: int = 0
    rating: float = 0.0
    plot: str = ""
    year: int = 0
    duration: int = 0
    genre: str = ""
    direct_source: str = ""

    @classmethod
    def from_api(cls, item: Dict[str, Any]) -> "Movie":
        return cls(
            id=_str(item.get("stream_id")),
            name=_str(item.get("name")) or "Unnamed",
            category_id=_str(item.get("category_id")),
            icon=_str(item.get("stream_icon")),
            container_extension=_str(item.get("container_extension"), "mp4") or "mp4",
            added=_int(item.get("added")),
            rating=_float(item.get("rating")),
            plot=_str(item.get("plot")),
            year=_int(item.get("year")),
            genre=_str(item.get("genre")),
            direct_source=_str(item.get("direct_source")),
        )


@dataclass(frozen=True)
class Series:
    id: str
    name: str
    category_id: str = ""
    cover: str = ""
    plot: str = ""
    genre: str = ""
    rating: float = 0.0
    year: int = 0
    last_modified: int = 0

    @classmethod
    def from_api(cls, item: Dict[str, Any]) -> "Series":
        return cls(
            id=_str(item.get("series_id")),
            name=_str(item.get("name")) or "Unnamed",
            category_id=_str(item.get("category_id")),
            cover=_str(item.get("cover")),
            plot=_str(item.get("plot")),
            genre=_str(item.get("genre")),
            rating=_float(item.get("rating")),
            year=_int(_str(item.get("releaseDate"))[:4]),
            last_modified=_int(item.get("last_modified")),
        )


@dataclass(frozen=True)
class Episode:
    id: str
    series_id: str
    season: int
    episode: int
    title: str
    container_extension: str = "mp4"
    plot: str = ""
    duration: int = 0
    added: int = 0
    thumb: str = ""
    rating: float = 0.0

    @classmethod
    def from_api(cls, item: Dict[str, Any], series_id: str, season: int) -> "Episode":
        info = item.get("info") or {}
        duration = _int(info.get("duration_secs"))
        if not duration:
            duration = _hms_to_seconds(_str(info.get("duration")))
        return cls(
            id=_str(item.get("id")),
            series_id=series_id,
            season=_int(item.get("season"), season),
            episode=_int(item.get("episode_num")),
            title=_str(item.get("title")) or "Unnamed",
            container_extension=_str(item.get("container_extension"), "mp4") or "mp4",
            plot=_str(info.get("plot")),
            duration=duration,
            added=_int(info.get("added")),
            thumb=_str(info.get("movie_image")),
            rating=_float(info.get("rating")),
        )


@dataclass(frozen=True)
class Programme:
    start: int
    stop: int
    title: str
    description: str = ""
    channel_id: str = ""


@dataclass(frozen=True)
class StreamRef:
    """A playable URL plus everything needed to actually play it.

    ``headers`` is not optional decoration: the reference provider refuses media
    requests without a User-Agent (HTTP 454), so the UI layer must apply these.
    """

    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    mime_type: str = ""
    inputstream: str = ""
    alternatives: List[str] = field(default_factory=list)
    # Only a live broadcast may use ffmpegdirect's realtime/timeshift mode; a VOD
    # movie or episode is a static file even when its container happens to be .ts.
    live: bool = False

    def with_url(self, url: str) -> "StreamRef":
        return StreamRef(
            url=url,
            headers=dict(self.headers),
            mime_type=self.mime_type,
            inputstream=self.inputstream,
            alternatives=list(self.alternatives),
            live=self.live,
        )


def _hms_to_seconds(value: str) -> int:
    """Parse the ``HH:MM:SS`` duration Xtream sometimes returns instead of seconds."""
    parts = value.split(":")
    if len(parts) != 3:
        return 0
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return 0
    return hours * 3600 + minutes * 60 + seconds


@dataclass
class Capabilities:
    live: bool = True
    vod: bool = False
    series: bool = False
    short_epg: bool = False
    xmltv_url: bool = False


@dataclass
class ExportResult:
    path: str
    channel_count: int = 0
    group_count: int = 0
    skipped: List[str] = field(default_factory=list)
    bytes_written: int = 0

    def summary(self) -> str:
        text = "%d channels in %d groups" % (self.channel_count, self.group_count)
        if self.skipped:
            text += ", %d skipped" % len(self.skipped)
        return text


@dataclass
class ProbeResult:
    url: str
    status: int = 0
    content_type: str = ""
    bytes_read: int = 0
    final_url: str = ""
    redirects: int = 0
    elapsed: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300
