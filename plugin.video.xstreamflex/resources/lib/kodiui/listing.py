"""ListItem construction.

Uses the Kodi 20+ InfoTag setters rather than the removed ``ListItem.setInfo``
dictionary API, so this works on Omega without deprecation spam.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import xbmcgui
import xbmcplugin

from core.http import header_suffix
from core.models import Channel, Episode, Movie, Series


def url_for(base_url: str, action: str, **params) -> str:
    query = {"action": action}
    query.update({k: v for k, v in params.items() if v not in (None, "")})
    return "%s?%s" % (base_url.rstrip("?"), urlencode(query))


def _art_url(url: str, headers: Optional[Dict[str, str]] = None) -> str:
    """Append the provider's required headers to an artwork URL.

    Kodi fetches poster/thumb images itself, outside the player hand-off, so a
    provider that refuses requests without a User-Agent (see PROVIDER-FINDINGS.md)
    refuses the poster too unless the header rides along in the same ``|Key=value``
    form used for playback URLs.
    """
    if not url:
        return ""
    return url + header_suffix(headers or {})


def _apply_video_info(item: xbmcgui.ListItem, *, title: str, plot: str = "",
                      mediatype: str = "video", year: int = 0, rating: float = 0.0,
                      duration: int = 0, genre: str = "", season: int = 0,
                      episode: int = 0, tvshowtitle: str = "") -> None:
    tag = item.getVideoInfoTag()
    tag.setTitle(title)
    tag.setMediaType(mediatype)
    if plot:
        tag.setPlot(plot)
    if year:
        tag.setYear(year)
    if rating:
        tag.setRating(rating)
    if duration:
        tag.setDuration(duration)
    if genre:
        tag.setGenres([g.strip() for g in genre.split(",") if g.strip()])
    if season:
        tag.setSeason(season)
    if episode:
        tag.setEpisode(episode)
    if tvshowtitle:
        tag.setTvShowTitle(tvshowtitle)


def folder_item(label: str, url: str, *, icon: str = "", plot: str = "",
                fanart: str = "",
                headers: Optional[Dict[str, str]] = None) -> Tuple[str, xbmcgui.ListItem, bool]:
    item = xbmcgui.ListItem(label=label, offscreen=True)
    art = {}
    if icon:
        art.update({"icon": _art_url(icon, headers), "thumb": _art_url(icon, headers)})
    if fanart:
        art["fanart"] = _art_url(fanart, headers)
    if art:
        item.setArt(art)
    if plot:
        _apply_video_info(item, title=label, plot=plot)
    return url, item, True


def movie_item(base_url: str, movie: Movie,
              headers: Optional[Dict[str, str]] = None) -> Tuple[str, xbmcgui.ListItem, bool]:
    item = xbmcgui.ListItem(label=movie.name, offscreen=True)
    icon = _art_url(movie.icon, headers)
    item.setArt({"icon": icon, "thumb": icon, "poster": icon})
    _apply_video_info(
        item, title=movie.name, plot=movie.plot, mediatype="movie",
        year=movie.year, rating=movie.rating, duration=movie.duration, genre=movie.genre,
    )
    item.setProperty("IsPlayable", "true")
    url = url_for(base_url, "play_movie", movie_id=movie.id,
                  ext=movie.container_extension, title=movie.name)
    return url, item, False


def series_item(base_url: str, series: Series,
                headers: Optional[Dict[str, str]] = None) -> Tuple[str, xbmcgui.ListItem, bool]:
    item = xbmcgui.ListItem(label=series.name, offscreen=True)
    cover = _art_url(series.cover, headers)
    item.setArt({"icon": cover, "thumb": cover, "poster": cover})
    _apply_video_info(
        item, title=series.name, plot=series.plot, mediatype="tvshow",
        year=series.year, rating=series.rating, genre=series.genre,
    )
    return url_for(base_url, "seasons", series_id=series.id), item, True


def episode_item(base_url: str, episode: Episode, show_title: str = "",
                 headers: Optional[Dict[str, str]] = None) -> Tuple[str, xbmcgui.ListItem, bool]:
    label = "%dx%02d. %s" % (episode.season, episode.episode, episode.title)
    item = xbmcgui.ListItem(label=label, offscreen=True)
    if episode.thumb:
        thumb = _art_url(episode.thumb, headers)
        item.setArt({"icon": thumb, "thumb": thumb})
    _apply_video_info(
        item, title=episode.title, plot=episode.plot, mediatype="episode",
        rating=episode.rating, duration=episode.duration,
        season=episode.season, episode=episode.episode, tvshowtitle=show_title,
    )
    item.setProperty("IsPlayable", "true")
    url = url_for(base_url, "play_episode", episode_id=episode.id,
                  ext=episode.container_extension, title=episode.title)
    return url, item, False


def channel_item(base_url: str, channel: Channel, now_next: str = "",
                 headers: Optional[Dict[str, str]] = None) -> Tuple[str, xbmcgui.ListItem, bool]:
    label = channel.name
    if now_next:
        label = "%s  ·  %s" % (channel.name, now_next)
    item = xbmcgui.ListItem(label=label, offscreen=True)
    if channel.logo:
        logo = _art_url(channel.logo, headers)
        item.setArt({"icon": logo, "thumb": logo})
    _apply_video_info(item, title=channel.name, plot=now_next)
    item.setProperty("IsPlayable", "true")
    url = url_for(base_url, "play_channel", channel_id=channel.id, title=channel.name)
    return url, item, False


def finish(handle: int, items: Iterable[Tuple[str, xbmcgui.ListItem, bool]],
           content: str = "", sort_methods: Optional[List[int]] = None) -> None:
    entries = list(items)
    if content:
        xbmcplugin.setContent(handle, content)
    for method in (sort_methods or []):
        xbmcplugin.addSortMethod(handle, method)
    xbmcplugin.addDirectoryItems(handle, entries, len(entries))
    xbmcplugin.endOfDirectory(handle)
