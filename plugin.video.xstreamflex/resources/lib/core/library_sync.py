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

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
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


def collect_movie_details(provider, movies: Iterable[Tuple[str, Movie]],
                           progress: Optional[ProgressFn] = None) -> Dict[str, dict]:
    """One ``get_vod_info`` call per movie, keyed by movie id.

    The VOD *list* endpoint is far thinner than it looks: it carries name,
    rating, icon and a tmdb id, and that is all - no plot, no year, no genre,
    no runtime. Everything that makes a library entry worth browsing (and
    ``<premiered>``, without which Kodi cannot offer "Release date" as a sort
    order at all) lives only behind the per-movie detail call.

    Measured at ~0.13s per movie against a real account, so roughly 12 minutes
    for a 5.5k catalogue on a cold cache and near-instant afterwards. That is
    the same shape as collect_shows_with_episodes, which already pays one call
    per show, and it runs on the service thread where nothing is waiting on it.

    A failure for one movie is skipped, not fatal: a thinner NFO is better than
    no library.
    """
    seen: Dict[str, dict] = {}
    unique = []
    for _source, movie in movies:
        if movie.id not in seen:
            seen[movie.id] = {}
            unique.append(movie)
    for index, movie in enumerate(unique, 1):
        if progress:
            progress(index, len(unique), movie.name)
        try:
            info = provider.movie_info(movie.id) or {}
        except Exception:
            continue
        details = info.get("info") if isinstance(info, dict) else None
        if isinstance(details, dict):
            seen[movie.id] = details
    return seen


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


def movie_nfo_content(movie: Movie, sources, headers: Optional[Dict[str, str]] = None,
                       details: Optional[dict] = None) -> str:
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
    d = details or {}

    def value(*keys):
        for key in keys:
            got = d.get(key)
            if got not in (None, "", 0, "0", []):
                return got
        return None

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<movie>"]
    lines.append("  <title>%s</title>" % _xml_escape(movie.name))

    plot = value("plot", "description") or movie.plot
    if plot:
        lines.append("  <plot>%s</plot>" % _xml_escape(str(plot)))

    # <premiered> is the whole reason for the detail call: Kodi derives its
    # "Release date" sort order from this, and without it the only meaningful
    # ordering left is by title. <year> alone does not give you that.
    released = value("releasedate", "release_date")
    year = movie.year
    if released:
        released = str(released)[:10]
        lines.append("  <premiered>%s</premiered>" % _xml_escape(released))
        if not year and len(released) >= 4 and released[:4].isdigit():
            year = int(released[:4])
    if year:
        lines.append("  <year>%d</year>" % year)

    genre = value("genre") or movie.genre
    if genre:
        # The provider comma-separates these; Kodi wants one element each.
        for part in [g.strip() for g in str(genre).split(",") if g.strip()]:
            lines.append("  <genre>%s</genre>" % _xml_escape(part))

    director = value("director")
    if director:
        for part in [x.strip() for x in str(director).split(",") if x.strip()]:
            lines.append("  <director>%s</director>" % _xml_escape(part))

    country = value("country")
    if country:
        for part in [x.strip() for x in str(country).split(",") if x.strip()]:
            lines.append("  <country>%s</country>" % _xml_escape(part))

    runtime_secs = value("duration_secs")
    if runtime_secs:
        try:
            lines.append("  <runtime>%d</runtime>" % round(int(runtime_secs) / 60))
        except (TypeError, ValueError):
            pass

    rating = value("rating") or movie.rating
    if rating:
        lines.append("  <rating>%s</rating>" % _xml_escape(str(rating)))

    tmdb_id = value("tmdb_id")
    if tmdb_id:
        # Lets Kodi (or a scraper run later) identify the film exactly instead
        # of guessing from a title decorated with resolution/country suffixes.
        lines.append('  <uniqueid type="tmdb">%s</uniqueid>' % _xml_escape(str(tmdb_id)))

    trailer = value("youtube_trailer")
    if trailer:
        lines.append("  <trailer>%s</trailer>" % _xml_escape(str(trailer)))

    for actor in [a.strip() for a in str(value("cast", "actors") or "").split(",") if a.strip()]:
        lines.append("  <actor>")
        lines.append("    <name>%s</name>" % _xml_escape(actor))
        lines.append("  </actor>")

    poster = value("movie_image", "cover_big") or movie.icon
    if poster:
        lines.append('  <thumb aspect="poster">%s</thumb>'
                     % _xml_escape(str(poster) + header_suffix(headers)))

    backdrops = d.get("backdrop_path") or []
    if isinstance(backdrops, str):
        backdrops = [backdrops]
    if backdrops:
        lines.append("  <fanart>")
        for art in backdrops[:3]:
            lines.append("    <thumb>%s</thumb>"
                         % _xml_escape(str(art) + header_suffix(headers)))
        lines.append("  </fanart>")

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


def episode_nfo_path(root: str, source: str, show_name: str, episode: Episode) -> str:
    """Same base name as episode_strm_path, ``.nfo`` instead of ``.strm``."""
    strm_path = episode_strm_path(root, source, show_name, episode)
    return strm_path[: -len(".strm")] + ".nfo"


def episode_nfo_content(episode: Episode, headers: Optional[Dict[str, str]] = None) -> str:
    """One NFO per episode. Without this, Kodi adds the *show* but not a single
    episode, and the library looks silently half-broken.

    Kodi's scanner (VideoInfoScanner::OnProcessSeriesFolder) only reaches
    AddVideo() directly when a per-episode NFO is found. With no NFO it falls
    through to matching the file against an episode guide, and:

      * an online scraper needs ``<episodeguide>`` in tvshow.nfo to fetch one -
        which show_nfo_content deliberately does not emit, because these shows
        are provider catalogue entries that frequently do not match TMDB at all
        (same reason movie_nfo_content exists);
      * the local scraper is excluded from guide fetching outright
        (``scraper->ID() != "metadata.local"``).

    Either way it hits ``if (episodes.empty()) { ...; continue; }`` and logs
    "Asked to lookup episode ... online, but we have either no episode guide or
    we are using the local scraper", skipping every episode. Shipping the NFO
    takes that whole path out of play, and lets the source be scanned with
    "Local information only" so a sync never touches the network.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             "<episodedetails>"]
    lines.append("  <title>%s</title>" % _xml_escape(episode.title))
    lines.append("  <season>%d</season>" % episode.season)
    lines.append("  <episode>%d</episode>" % episode.episode)
    if episode.plot:
        lines.append("  <plot>%s</plot>" % _xml_escape(episode.plot))
    if episode.rating:
        lines.append("  <rating>%s</rating>" % episode.rating)
    if episode.duration:
        # Kodi reads <runtime> in whole minutes; the provider gives seconds.
        lines.append("  <runtime>%d</runtime>" % round(episode.duration / 60))
    if episode.thumb:
        lines.append('  <thumb>%s</thumb>'
                     % _xml_escape(episode.thumb + header_suffix(headers)))
    lines.append("</episodedetails>")
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
                 headers: Optional[Dict[str, str]] = None,
                 details: Optional[Dict[str, dict]] = None) -> Tuple[int, int]:
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
        wanted[movie_nfo_path(root, primary_source, movie)] = movie_nfo_content(
            movie, sources, headers, (details or {}).get(movie.id))
    written = 0
    for path, content in wanted.items():
        try:
            if _write_if_changed(path, content):
                written += 1
        except (OSError, UnicodeError) as exc:
            # One bad path (historically: a provider-supplied title long enough
            # to push the full path past Windows' MAX_PATH even after capping,
            # or a permissions issue) must not take the rest of a catalogue of
            # thousands of items down with it.
            #
            # UnicodeError is not an OSError, so it used to escape this guard and
            # abort the whole sync. It happens whenever Python's filesystem
            # encoding is ASCII and a title contains anything outside it - which
            # is not exotic: Steam exports LC_ALL=C to everything it launches, so
            # Kodi started from a Steam shortcut hits this on the first accented
            # film title. Fixing the locale is the real cure; surviving it is
            # this loop's job.
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
            wanted[episode_nfo_path(root, source, show.name, episode)] = \
                episode_nfo_content(episode, headers)
    written = 0
    for path, content in wanted.items():
        try:
            if _write_if_changed(path, content):
                written += 1
        except (OSError, UnicodeError) as exc:
            # Same reasoning as sync_movies: an un-encodable path is a skipped
            # episode, not a failed sync.
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
        # Written via a temp file + rename, which is atomic on POSIX. A plain
        # open("w") is not: two syncs finishing together (the scheduled one and
        # a manual one) interleaved their writes and left trailing braces in
        # the JSON. last_sync_time() then read that as ValueError -> 0 ->
        # "never synced" -> a full sync on *every* startup, forever. The lock
        # below makes concurrent syncs impossible; this makes a torn write
        # impossible even if a process dies mid-save.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


#: Held for the whole of a sync. Two syncs at once do the same tens of
#: thousands of writes twice and race on the state file - which is exactly
#: what happened live: the service's scheduled sync and a manual one from the
#: menu both finished in the same second, on different threads.
LOCK_FILE = "library-sync.lock"

#: Written by the menu action, consumed by the service. A sync of this
#: catalogue takes minutes, and Kodi resolves a plugin:// directory listing
#: with a timeout while showing a modal busy spinner - so running it inline
#: ends in "GetDirectory - Error getting plugin://..." and a spinner that
#: never goes away. The work belongs on the service thread instead.
REQUEST_FILE = "library-sync-requested"


@contextmanager
def sync_lock(state_dir: str):
    """Yields True if this caller got the lock, False if a sync is already
    running. Never blocks: the caller decides whether to skip or report.

    flock is released by the kernel when the process dies or the fd closes, so
    a crashed sync cannot leave a lock behind that needs manual clearing.
    """
    try:
        os.makedirs(state_dir, exist_ok=True)
        handle = open(os.path.join(state_dir, LOCK_FILE), "w")
    except OSError:
        # Can't create the lock file - run unlocked rather than never syncing.
        yield True
        return
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def request_sync(state_dir: str) -> None:
    """Ask the service to sync on its next tick."""
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, REQUEST_FILE), "w") as handle:
            handle.write("1")
    except OSError:
        pass


def take_sync_request(state_dir: str) -> bool:
    """Consume a pending request, if any. Removing it before the sync runs (not
    after) means a request that crashes the sync is not retried forever."""
    try:
        os.remove(os.path.join(state_dir, REQUEST_FILE))
        return True
    except OSError:
        return False
