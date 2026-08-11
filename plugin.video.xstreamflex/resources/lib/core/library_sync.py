"""Writes ``.strm`` files so the catalogue can join Kodi's video library.

Kodi's documented way to put an add-on's own listing directly into the library
(Settings > Media > Videos > Add videos, pointed at a ``plugin://`` URL, with a
content type set) is real, but in practice unreliable for a catalogue this size:
the scan either never triggers or finishes in milliseconds having walked nothing,
with no add-on-side visibility into why.

``.strm`` files sidestep plugin-source scanning entirely: a folder of them is, as
far as Kodi's library scanner is concerned, indistinguishable from a folder of
real video files that some other tool (Sonarr, a download client, ...) put there
- the best-trodden path in the whole Kodi ecosystem, used by every add-on that
offers "add to library". Each file is one line: the ``plugin://`` URL Kodi should
open when it "plays" that "file".
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from .models import SERIES, VOD, Episode, Movie

_UNSAFE = re.compile(r'[\\/:*?"<>|]')

#: Separate from core.export.exporter's export-state.json - conflating "when did
#: the channel M3U last get rebuilt" with "when did the library .strm files last
#: get rebuilt" would make either one unable to report accurately on its own.
STATE_FILE = "library-sync-state.json"

ProgressFn = Callable[[int, int, str], None]


def matches_country(category_name: str, country: str) -> bool:
    """Matches the provider's own "NL | Filmclub 2026" naming: the country code
    at the very start, whatever punctuation follows it (``|``, ``-``, ``:``, a
    plain space). Case-insensitive since panels are not consistent about it.
    Requires a non-letter right after the code (or nothing) so "NLD | ..." does
    not match a filter for "NL"."""
    name = category_name.strip()
    prefix = name[:len(country)]
    if prefix.casefold() != country.casefold():
        return False
    return not name[len(country):len(country) + 1].isalnum()


def collect_movies(provider, country: str, progress: Optional[ProgressFn] = None) -> List[Movie]:
    categories = [c for c in provider.categories(VOD) if matches_country(c.name, country)]
    movies: List[Movie] = []
    for index, category in enumerate(categories, 1):
        if progress:
            progress(index, len(categories), category.name)
        movies.extend(provider.movies(category.id))
    return movies


def collect_shows_with_episodes(
    provider, country: str, progress: Optional[ProgressFn] = None
) -> List[Tuple[str, List[Episode]]]:
    """One provider call per show (no bulk "all episodes" endpoint exists) -
    the slow part of a library sync, hence its own progress callback."""
    categories = [c for c in provider.categories(SERIES) if matches_country(c.name, country)]
    shows = []
    for category in categories:
        shows.extend(provider.series(category.id))

    result = []
    for index, show in enumerate(shows, 1):
        if progress:
            progress(index, len(shows), show.name)
        _, episodes = provider.series_info(show.id)
        result.append((show.name, episodes))
    return result


def safe_filename(name: str, fallback: str = "untitled") -> str:
    """Strip characters a Windows or POSIX filesystem would reject."""
    cleaned = _UNSAFE.sub(" ", name or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def movie_strm_path(root: str, movie: Movie) -> str:
    return os.path.join(root, "%s (%s).strm" % (safe_filename(movie.name), movie.id))


def movie_strm_content(base_url: str, movie: Movie) -> str:
    return "%s?action=play_movie&movie_id=%s&ext=%s&title=%s" % (
        base_url.rstrip("/"), quote(movie.id, safe=""),
        quote(movie.container_extension or "mp4", safe=""),
        quote(movie.name, safe=""),
    )


def episode_strm_path(root: str, show_name: str, episode: Episode) -> str:
    show_dir = safe_filename(show_name)
    filename = "%s - S%02dE%02d - %s (%s).strm" % (
        show_dir, episode.season, episode.episode,
        safe_filename(episode.title, "Episode"), episode.id,
    )
    return os.path.join(root, show_dir, filename)


def episode_strm_content(base_url: str, episode: Episode) -> str:
    return "%s?action=play_episode&episode_id=%s&ext=%s&title=%s" % (
        base_url.rstrip("/"), quote(episode.id, safe=""),
        quote(episode.container_extension or "mp4", safe=""),
        quote(episode.title, safe=""),
    )


def _write_if_changed(path: str, content: str) -> bool:
    """Returns whether the file was actually written.

    Comparing first avoids bumping every file's mtime on every sync, which would
    make Kodi's own incremental library scan treat the whole catalogue as changed
    each time instead of only what's actually new.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() == content:
                return False
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return True


def _prune(root: str, wanted: Dict[str, str]) -> int:
    """Removes every ``.strm`` under ``root`` that is not in ``wanted``.

    A title dropped by the provider (or that fell out of the country filter)
    should disappear from Kodi's library on the next sync, the same way deleting
    a downloaded file does - not linger as a dead link forever.
    """
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".strm"):
                continue
            path = os.path.join(dirpath, name)
            if path not in wanted:
                os.remove(path)
                removed += 1
    return removed


def sync_movies(root: str, movies: Iterable[Movie], base_url: str) -> Tuple[int, int]:
    """Returns ``(written, removed)`` file counts."""
    os.makedirs(root, exist_ok=True)
    wanted = {movie_strm_path(root, movie): movie_strm_content(base_url, movie)
              for movie in movies}
    written = sum(1 for path, content in wanted.items() if _write_if_changed(path, content))
    removed = _prune(root, wanted)
    return written, removed


def sync_episodes(root: str, shows: Iterable[Tuple[str, Iterable[Episode]]],
                   base_url: str) -> Tuple[int, int]:
    """``shows``: pairs of (show name, that show's episodes)."""
    os.makedirs(root, exist_ok=True)
    wanted: Dict[str, str] = {}
    for show_name, episodes in shows:
        for episode in episodes:
            path = episode_strm_path(root, show_name, episode)
            wanted[path] = episode_strm_content(base_url, episode)
    written = sum(1 for path, content in wanted.items() if _write_if_changed(path, content))
    removed = _prune(root, wanted)
    return written, removed


def last_sync_time(state_dir: str, provider_id: str) -> int:
    try:
        with open(os.path.join(state_dir, STATE_FILE), "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return int(state.get(provider_id, {}).get("at", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def is_sync_stale(state_dir: str, provider_id: str, max_age_seconds: int) -> bool:
    last = last_sync_time(state_dir, provider_id)
    return not last or (time.time() - last) >= max_age_seconds


def write_sync_state(state_dir: str, provider_id: str, *, movies_written: int,
                      movies_removed: int, series_written: int, series_removed: int) -> None:
    path = os.path.join(state_dir, STATE_FILE)
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
        "movies_written": movies_written, "movies_removed": movies_removed,
        "series_written": series_written, "series_removed": series_removed,
    }
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
    except OSError:
        pass
