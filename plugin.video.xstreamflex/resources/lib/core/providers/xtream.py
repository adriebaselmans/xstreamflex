"""Xtream Codes client.

Deliberately never touches ``get.php``. On the reference provider that endpoint
answers 885 with an empty body while the API below works perfectly — which is the
whole reason this project exists. See docs/PROVIDER-FINDINGS.md.

Panels are also inconsistent about response shapes: a list endpoint may return an
object, ``false``, or an error dictionary, and numbers arrive as strings. Every
parser here coerces explicitly and raises ParseError rather than letting a TypeError
escape from somewhere deep in the UI.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from ..cache import (
    Cache,
    NullCache,
    TTL_ACCOUNT,
    TTL_CATEGORIES,
    TTL_CHANNELS,
    TTL_METADATA,
    TTL_SHORT_EPG,
    cached,
)
from ..config import ProviderConfig
from ..errors import AuthError, ParseError
from ..http import HttpClient
from ..models import (
    LIVE,
    SERIES,
    VOD,
    Account,
    Capabilities,
    Category,
    Channel,
    Episode,
    Movie,
    Programme,
    Series,
    StreamRef,
    _int,
    _str,
)
from .base import BaseProvider

CATEGORY_ACTIONS = {
    LIVE: "get_live_categories",
    VOD: "get_vod_categories",
    SERIES: "get_series_categories",
}


class XtreamProvider(BaseProvider):
    kind = "xtream"
    capabilities = Capabilities(live=True, vod=True, series=True, short_epg=True, xmltv_url=True)

    def __init__(self, config: ProviderConfig, client: HttpClient,
                 cache: Optional[Cache] = None, logger=None) -> None:
        self.config = config
        self.client = client
        self.cache = cache or NullCache()
        self._log = logger or (lambda level, message: None)

    # -- plumbing --------------------------------------------------------

    def _key(self, *parts: str) -> str:
        return ":".join(("xtream", self.config.id) + parts)

    def _call(self, action: str = "", **params: Any) -> Any:
        query: Dict[str, Any] = {
            "username": self.config.username,
            "password": self.config.password,
        }
        if action:
            query["action"] = action
        query.update({k: v for k, v in params.items() if v not in (None, "")})
        return self.client.get_json(self.config.api_url, params=query)

    @staticmethod
    def _as_list(payload: Any, what: str) -> List[Dict[str, Any]]:
        """Coerce a list endpoint's answer, tolerating the shapes panels really send."""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if payload in (None, False, ""):
            return []  # empty category, not an error
        if isinstance(payload, dict):
            # Some panels wrap the list, others answer with an error object.
            for key in ("data", "categories", "streams", "result"):
                inner = payload.get(key)
                if isinstance(inner, list):
                    return [item for item in inner if isinstance(item, dict)]
            message = payload.get("error") or payload.get("message")
            if message:
                raise ParseError("Provider reported an error for %s." % what, str(message)[:200])
            return []
        raise ParseError(
            "Provider returned an unexpected shape for %s." % what,
            "type=%s" % type(payload).__name__,
        )

    # -- account ---------------------------------------------------------

    def account(self) -> Account:
        payload = cached(
            self.cache, self._key("account"), TTL_ACCOUNT,
            lambda: self._call(), self._log,
        )
        if not isinstance(payload, dict):
            raise ParseError("Provider did not return account information.")
        account = Account.from_api(payload)
        if not account.auth:
            raise AuthError(
                "The provider rejected these credentials.",
                "auth=0 in player_api response",
            )
        return account

    # -- browsing --------------------------------------------------------

    def categories(self, kind: str) -> List[Category]:
        action = CATEGORY_ACTIONS.get(kind)
        if action is None:
            raise ParseError("Unknown category kind %r." % kind)
        raw = cached(
            self.cache, self._key("categories", kind), TTL_CATEGORIES,
            lambda: self._as_list(self._call(action), "%s categories" % kind), self._log,
        )
        return [Category.from_api(item, kind) for item in raw]

    def channels(self, category_id: Optional[str] = None) -> List[Channel]:
        if category_id:
            return [Channel.from_api(item) for item in self._channel_rows(category_id)]
        # No bulk endpoint is used on purpose: one request per category keeps each
        # response small, individually cacheable, and retryable in isolation.
        result: List[Channel] = []
        for category in self.categories(LIVE):
            result.extend(Channel.from_api(item) for item in self._channel_rows(category.id))
        return result

    def iter_channels(self, categories: Optional[Iterable[Category]] = None,
                      progress=None) -> Iterable[Channel]:
        """Yield channels category by category, reporting progress as it goes.

        The exporter uses this so a 262-category account shows movement instead of
        appearing frozen for a minute.
        """
        cats = list(categories if categories is not None else self.categories(LIVE))
        total = len(cats) or 1
        for index, category in enumerate(cats, start=1):
            if progress is not None:
                progress(index, total, category.name)
            for item in self._channel_rows(category.id):
                channel = Channel.from_api(item)
                yield replace(
                    channel,
                    category_id=channel.category_id or category.id,
                    group=category.name,
                )

    def _channel_rows(self, category_id: str) -> List[Dict[str, Any]]:
        return cached(
            self.cache, self._key("channels", category_id), TTL_CHANNELS,
            lambda: self._as_list(
                self._call("get_live_streams", category_id=category_id), "channels"
            ),
            self._log,
        )

    def movies(self, category_id: Optional[str] = None) -> List[Movie]:
        if category_id:
            rows = cached(
                self.cache, self._key("movies", category_id), TTL_CHANNELS,
                lambda: self._as_list(
                    self._call("get_vod_streams", category_id=category_id), "movies"
                ),
                self._log,
            )
            return [Movie.from_api(item) for item in rows]
        result: List[Movie] = []
        for category in self.categories(VOD):
            result.extend(self.movies(category.id))
        return result

    def movie_info(self, movie_id: str) -> dict:
        payload = cached(
            self.cache, self._key("movie_info", movie_id), TTL_METADATA,
            lambda: self._call("get_vod_info", vod_id=movie_id), self._log,
        )
        return payload if isinstance(payload, dict) else {}

    def series(self, category_id: Optional[str] = None) -> List[Series]:
        if category_id:
            rows = cached(
                self.cache, self._key("series", category_id), TTL_CHANNELS,
                lambda: self._as_list(
                    self._call("get_series", category_id=category_id), "series"
                ),
                self._log,
            )
            return [Series.from_api(item) for item in rows]
        result: List[Series] = []
        for category in self.categories(SERIES):
            result.extend(self.series(category.id))
        return result

    def series_info(self, series_id: str) -> Tuple[Series, List[Episode]]:
        payload = cached(
            self.cache, self._key("series_info", series_id), TTL_METADATA,
            lambda: self._call("get_series_info", series_id=series_id), self._log,
        )
        if not isinstance(payload, dict):
            raise ParseError("Provider returned no details for this series.")

        info = payload.get("info") or {}
        show = Series(
            id=series_id,
            name=_str(info.get("name")) or "Unnamed",
            cover=_str(info.get("cover")),
            plot=_str(info.get("plot")),
            genre=_str(info.get("genre")),
            year=_int(_str(info.get("releaseDate"))[:4]),
        )

        episodes: List[Episode] = []
        seasons = payload.get("episodes") or {}
        if isinstance(seasons, list):
            # A few panels emit a list indexed by season instead of a mapping.
            seasons = {str(i): value for i, value in enumerate(seasons)}
        if isinstance(seasons, dict):
            for season_key, entries in seasons.items():
                if not isinstance(entries, list):
                    continue
                season_no = _int(season_key)
                for entry in entries:
                    if isinstance(entry, dict):
                        episodes.append(Episode.from_api(entry, series_id, season_no))
        episodes.sort(key=lambda e: (e.season, e.episode))
        return show, episodes

    def short_epg(self, channel: Channel, limit: int = 2) -> List[Programme]:
        """Best-effort "now / next".

        The reference provider returned an empty list for a channel that clearly has
        EPG data in its XMLTV feed, so nothing may depend on this succeeding.
        """
        payload = cached(
            self.cache, self._key("short_epg", channel.id, str(limit)), TTL_SHORT_EPG,
            lambda: self._call("get_short_epg", stream_id=channel.id, limit=limit),
            self._log,
        )
        listings: Any = []
        if isinstance(payload, dict):
            listings = payload.get("epg_listings") or []
        elif isinstance(payload, list):
            listings = payload
        result: List[Programme] = []
        for item in listings:
            if not isinstance(item, dict):
                continue
            result.append(Programme(
                start=_int(item.get("start_timestamp")),
                stop=_int(item.get("stop_timestamp")),
                title=_decode_epg_field(item.get("title")),
                description=_decode_epg_field(item.get("description")),
                channel_id=channel.epg_channel_id or channel.id,
            ))
        return result

    # -- stream URLs -----------------------------------------------------

    def _credentials_path(self) -> str:
        return "%s/%s" % (
            quote(self.config.username, safe=""),
            quote(self.config.password, safe=""),
        )

    def _headers(self) -> Dict[str, str]:
        headers = {"User-Agent": self.config.user_agent}
        if self.config.referer:
            headers["Referer"] = self.config.referer
        return headers

    def live_url(self, channel_id: str, extension: str = "") -> str:
        # The extension is mandatory: without it the panel answers 512.
        ext = extension or self.config.preferred_format or "ts"
        return "%s/live/%s/%s.%s" % (
            self.config.base_url, self._credentials_path(), quote(str(channel_id), safe=""), ext,
        )

    def live_stream(self, channel: Channel) -> StreamRef:
        preferred = self.config.preferred_format or "ts"
        fallback = "m3u8" if preferred == "ts" else "ts"
        alternatives = [self.live_url(channel.id, fallback)]
        if channel.direct_source:
            alternatives.append(channel.direct_source)
        return StreamRef(
            url=self.live_url(channel.id, preferred),
            headers=self._headers(),
            mime_type="video/mp2t" if preferred == "ts" else "",
            alternatives=alternatives,
            live=True,
        )

    def movie_stream(self, movie: Movie) -> StreamRef:
        extension = movie.container_extension or "mp4"
        url = "%s/movie/%s/%s.%s" % (
            self.config.base_url, self._credentials_path(),
            quote(str(movie.id), safe=""), extension,
        )
        alternatives = [
            url.rsplit(".", 1)[0] + "." + alt
            for alt in ("mkv", "mp4", "avi") if alt != extension
        ]
        if movie.direct_source:
            alternatives.insert(0, movie.direct_source)
        return StreamRef(url=url, headers=self._headers(), alternatives=alternatives)

    def episode_stream(self, episode: Episode) -> StreamRef:
        extension = episode.container_extension or "mp4"
        url = "%s/series/%s/%s.%s" % (
            self.config.base_url, self._credentials_path(),
            quote(str(episode.id), safe=""), extension,
        )
        alternatives = [
            url.rsplit(".", 1)[0] + "." + alt
            for alt in ("mkv", "mp4", "avi") if alt != extension
        ]
        return StreamRef(url=url, headers=self._headers(), alternatives=alternatives)


def _decode_epg_field(value: Any) -> str:
    """Xtream base64-encodes EPG titles and descriptions. Usually."""
    text = _str(value)
    if not text:
        return ""
    import base64
    import binascii
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return text
    try:
        return decoded.decode("utf-8").strip()
    except UnicodeDecodeError:
        return text
