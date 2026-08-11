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
from xml.sax.saxutils import escape as _xml_escape

from .http import header_suffix
from .models import SERIES, VOD, Episode, Movie, Series

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


def collect_movies(
    provider, country: str, progress: Optional[ProgressFn] = None
) -> List[Tuple[str, Movie]]:
    """Returns ``(source category name, movie)`` pairs - one pair per occurrence,
    so a movie listed under several categories (e.g. both "NL | Netflix" and
    "NL | Videoland") carries every one of them. ``sync_movies`` is what
    collapses same-id occurrences back into a single movie with multiple
    ``<tag>`` entries - not this function, which stays a flat, order-preserving
    list of everything the provider returned."""
    categories = [c for c in provider.categories(VOD) if matches_country(c.name, country)]
    result: List[Tuple[str, Movie]] = []
    for index, category in enumerate(categories, 1):
        if progress:
            progress(index, len(categories), category.name)
        result.extend((category.name, movie) for movie in provider.movies(category.id))
    return result


def collect_shows_with_episodes(
    provider, country: str, progress: Optional[ProgressFn] = None
) -> List[Tuple[str, Series, List[Episode]]]:
    """One provider call per show (no bulk "all episodes" endpoint exists) -
    the slow part of a library sync, hence its own progress callback. Returns
    ``(source category name, show, episodes)`` triples - the full ``Series``
    object, not just its name, so ``tvshow.nfo`` generation has plot/cover/
    genre/rating/year to work with."""
    categories = [c for c in provider.categories(SERIES) if matches_country(c.name, country)]
    shows = []
    for category in categories:
        shows.extend((category.name, show) for show in provider.series(category.id))

    result = []
    for index, (source, show) in enumerate(shows, 1):
        if progress:
            progress(index, len(shows), show.name)
        _, episodes = provider.series_info(show.id)
        result.append((source, show, episodes))
    return result


#: Headroom under Windows' 260-character MAX_PATH. Kept well below it because
#: ``root`` (the add-on's profile directory) is itself often 100+ characters
#: before any of this module's own folders/filenames are added.
_MAX_PATH = 240
#: Never shrink a free-text component to less than this - past this point a
#: name is unrecognisable anyway, and the per-item try/except around every
#: write (see sync_movies/sync_episodes) is the real backstop if root's own
#: length alone already leaves no sane budget.
_MIN_COMPONENT = 8


def safe_filename(name: str, fallback: str = "untitled", max_length: int = 80) -> str:
    """Strip characters a Windows or POSIX filesystem would reject, and cap the
    length. Also strips trailing dots/spaces, which Windows rejects in a path
    component and plain ``.strip()`` does not catch.
    """
    cleaned = _UNSAFE.sub(" ", name or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or fallback


def _budget_for(root: str, *fixed_parts: str, separators: int) -> int:
    """How many characters are left for the one free-text component in a path,
    given everything else (``root`` plus every already-decided ``fixed_parts``)
    is fixed. Some providers put a fully-formatted string ("Show - S01E02 -
    Real Title") in what is nominally just a title field; combined with this
    module prepending the show name and episode code again, that can push a
    filename past Windows' 260-character MAX_PATH, which surfaces as a plain
    "No such file or directory" rather than anything that says "too long" -
    computing the budget from the *actual* root length, rather than a fixed
    per-component cap, is what makes this correct regardless of how long the
    add-on's own profile path happens to be on a given machine.
    """
    used = len(root) + sum(len(p) for p in fixed_parts) + separators
    return max(_MIN_COMPONENT, _MAX_PATH - used)


def _show_dir_name(show_name: str) -> str:
    """Shared between episode_strm_path and show_nfo_path so an episode and its
    show's tvshow.nfo always agree on which folder they live in."""
    return safe_filename(show_name, max_length=60)


def movie_strm_path(root: str, source: str, movie: Movie) -> str:
    source_dir = safe_filename(source, "Other", max_length=40)
    suffix = " (%s).strm" % movie.id
    budget = _budget_for(root, source_dir, suffix, separators=2)
    title = safe_filename(movie.name, max_length=budget)
    return os.path.join(root, source_dir, title + suffix)


def movie_strm_content(base_url: str, movie: Movie) -> str:
    return "%s?action=play_movie&movie_id=%s&ext=%s&title=%s" % (
        base_url.rstrip("/"), quote(movie.id, safe=""),
        quote(movie.container_extension or "mp4", safe=""),
        quote(movie.name, safe=""),
    )


def movie_nfo_path(root: str, source: str, movie: Movie) -> str:
    """Same base name as movie_strm_path, ``.nfo`` instead of ``.strm`` -
    Kodi's convention for matching a companion metadata file to a video file."""
    strm_path = movie_strm_path(root, source, movie)
    return strm_path[: -len(".strm")] + ".nfo"


def movie_nfo_content(movie: Movie, sources, headers: Optional[Dict[str, str]] = None) -> str:
    """Self-contained NFO: title/plot/art/tag straight from the provider, no
    online scraper match required. A real account showed many titles the
    provider itself supplies cleanly (e.g. "Aquaman and the Lost Kingdom")
    failing to match TMDB once decorated with a resolution/country/id suffix
    for filename-uniqueness reasons - "No information found ... it won't be
    added to the library" - so an item could sync correctly and still never
    appear in Kodi. This sidesteps matching entirely. ``<tag>`` is what lets
    Kodi's library view filter/browse by the provider's own "LAND | Source"
    category, the equivalent of the per-source folder movies already get,
    without depending on file/folder structure Kodi's scanner might not walk
    (see episode_nfo/show_nfo below for why series can't use a folder for this).

    ``sources`` accepts either a single category name (back-compat) or an
    iterable of them: a provider's own VOD categories often overlap heavily
    ("NL | Nieuw", "NL | Actie", "NL | Films 2026", ...), and a title can
    easily match a dozen of them at once. sync_movies collapses those into
    one movie with one ``<tag>`` per matching category, rather than a
    separate duplicate .strm/.nfo pair per category - see its docstring.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<movie>"]
    lines.append("  <title>%s</title>" % _xml_escape(movie.name))
    if movie.plot:
        lines.append("  <plot>%s</plot>" % _xml_escape(movie.plot))
    if movie.year:
        lines.append("  <year>%d</year>" % movie.year)
    if movie.genre:
        lines.append("  <genre>%s</genre>" % _xml_escape(movie.genre))
    if movie.rating:
        lines.append("  <rating>%s</rating>" % movie.rating)
    if movie.icon:
        lines.append('  <thumb aspect="poster">%s</thumb>'
                     % _xml_escape(movie.icon + header_suffix(headers)))
    source_list = [sources] if isinstance(sources, str) else list(sources)
    for source in source_list:
        lines.append("  <tag>%s</tag>" % _xml_escape(source))
    lines.append("</movie>")
    return "\n".join(lines) + "\n"


def episode_strm_path(root: str, source: str, show_name: str, episode: Episode) -> str:
    # No source folder here, unlike movie_strm_path: Kodi's TV scanner expects
    # <TV root>/<Show>/<episode file>, one fixed level of nesting, not an extra
    # "source" folder above the show. Confirmed on a real account - with a
    # source folder in between, Kodi logged "No information found for item
    # '...\<source folder>\'" for the *source* folder itself and never
    # descended into the actual show folders beneath it, leaving essentially
    # the entire series catalogue invisible in Kodi's library. Movies do not
    # have this problem; Kodi's movie scanner walks arbitrarily deep. The
    # source is instead recorded as a Kodi tag on the show - see show_nfo_path.
    show_dir = _show_dir_name(show_name)
    prefix = "S%02dE%02d - " % (episode.season, episode.episode)
    suffix = " (%s).strm" % episode.id
    budget = _budget_for(root, show_dir, prefix, suffix, separators=2)
    title = safe_filename(episode.title, "Episode", max_length=budget)
    filename = prefix + title + suffix
    return os.path.join(root, show_dir, filename)


def show_nfo_path(root: str, show_name: str) -> str:
    """Kodi's convention for a show's own metadata (as opposed to a single
    episode's): a fixed filename, ``tvshow.nfo``, directly in the show's
    folder - not matched to any one episode file."""
    return os.path.join(root, _show_dir_name(show_name), "tvshow.nfo")


def show_nfo_content(show: Series, source: str, headers: Optional[Dict[str, str]] = None) -> str:
    """One per show, not per episode - the source is a property of the show,
    and Kodi's TV tag filtering works at the show level. See movie_nfo_content
    for why a self-contained NFO is used at all rather than relying on an
    online scraper match."""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<tvshow>"]
    lines.append("  <title>%s</title>" % _xml_escape(show.name))
    if show.plot:
        lines.append("  <plot>%s</plot>" % _xml_escape(show.plot))
    if show.year:
        lines.append("  <year>%d</year>" % show.year)
    if show.genre:
        lines.append("  <genre>%s</genre>" % _xml_escape(show.genre))
    if show.rating:
        lines.append("  <rating>%s</rating>" % show.rating)
    if show.cover:
        lines.append('  <thumb aspect="poster">%s</thumb>'
                     % _xml_escape(show.cover + header_suffix(headers)))
    lines.append("  <tag>%s</tag>" % _xml_escape(source))
    lines.append("</tvshow>")
    return "\n".join(lines) + "\n"


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
    """Removes every ``.strm``/``.nfo`` under ``root`` that is not in ``wanted``.

    A title dropped by the provider (or that fell out of the country filter)
    should disappear from Kodi's library on the next sync, the same way deleting
    a downloaded file does - not linger as a dead link forever.
    """
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not (name.endswith(".strm") or name.endswith(".nfo")):
                continue
            path = os.path.join(dirpath, name)
            if path not in wanted:
                os.remove(path)
                removed += 1
    return removed


ErrorFn = Callable[[str, Exception], None]


def _noop_error(path: str, exc: Exception) -> None:  # pragma: no cover - default
    pass


def sync_movies(root: str, movies: Iterable[Tuple[str, Movie]], base_url: str,
                 on_error: ErrorFn = _noop_error,
                 headers: Optional[Dict[str, str]] = None) -> Tuple[int, int]:
    """``movies``: (source category name, movie) pairs. Returns ``(written, removed)``.

    A movie appearing under several *category* pairs collapses to a single
    .strm/.nfo pair, filed under whichever category it was first seen in, with
    every matching category folded into the NFO as its own ``<tag>``. Without
    this, a provider that scatters one title across many overlapping VOD
    categories (as opposed to genuinely distinct source apps like Netflix vs.
    Videoland) produces that many duplicate entries in Kodi's Movies view for
    what is really one film - the id, not the category, is what makes a movie
    unique.
    """
    os.makedirs(root, exist_ok=True)
    grouped: Dict[str, Tuple[str, Movie, List[str]]] = {}
    for source, movie in movies:
        entry = grouped.get(movie.id)
        if entry is None:
            grouped[movie.id] = (source, movie, [source])
        elif source not in entry[2]:
            entry[2].append(source)
    wanted: Dict[str, str] = {}
    for primary_source, movie, sources in grouped.values():
        wanted[movie_strm_path(root, primary_source, movie)] = movie_strm_content(base_url, movie)
        wanted[movie_nfo_path(root, primary_source, movie)] = movie_nfo_content(movie, sources, headers)
    written = 0
    for path, content in wanted.items():
        try:
            if _write_if_changed(path, content):
                written += 1
        except OSError as exc:
            # One bad path (historically: a provider-supplied title long enough
            # to push the full path past Windows' MAX_PATH even after capping,
            # or a permissions issue) must not take the rest of a catalogue of
            # thousands of items down with it.
            on_error(path, exc)
    removed = _prune(root, wanted)
    return written, removed


def sync_episodes(root: str, shows: Iterable[Tuple[str, Series, Iterable[Episode]]],
                   base_url: str, on_error: ErrorFn = _noop_error,
                   headers: Optional[Dict[str, str]] = None) -> Tuple[int, int]:
    """``shows``: (source category name, show, that show's episodes) triples."""
    os.makedirs(root, exist_ok=True)
    wanted: Dict[str, str] = {}
    for source, show, episodes in shows:
        wanted[show_nfo_path(root, show.name)] = show_nfo_content(show, source, headers)
        for episode in episodes:
            path = episode_strm_path(root, source, show.name, episode)
            wanted[path] = episode_strm_content(base_url, episode)
    written = 0
    for path, content in wanted.items():
        try:
            if _write_if_changed(path, content):
                written += 1
        except OSError as exc:
            on_error(path, exc)
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
