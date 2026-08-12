#!/usr/bin/env python3
"""Run a full library sync outside Kodi, against the add-on's own saved provider.

For applying a library_sync.py fix to an existing on-disk library without
launching Kodi first - reads providers.json directly from the add-on's profile
directory instead of prompting for credentials, then calls the exact same
collect_movies/sync_movies/collect_shows_with_episodes/sync_episodes pipeline
router.py's sync_library route uses.

    python tools/run_library_sync.py --profile "<Kodi userdata>/addon_data/plugin.video.xstreamflex"
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.xstreamflex", "resources", "lib"))

from core.cache import NullCache  # noqa: E402
from core.config import ProviderStore  # noqa: E402
from core.http import HttpClient  # noqa: E402
from core.library_sync import (  # noqa: E402
    collect_movies,
    collect_shows_with_episodes,
    sync_episodes,
    sync_movies,
    write_sync_state,
)
from core.providers.xtream import XtreamProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True,
                        help="Add-on profile dir, e.g. .../addon_data/plugin.video.xstreamflex")
    parser.add_argument("--country", default="NL")
    args = parser.parse_args()

    store = ProviderStore(os.path.join(args.profile, "providers.json"))
    config = store.active()
    if config is None or not config.is_complete:
        print("No complete active provider found in %s" % args.profile)
        return 1
    print("Syncing against: %s" % config.describe())

    log = lambda level, message: print("[%s] %s" % (level, message))  # noqa: E731
    client = HttpClient(config.user_agent, secrets=config.secrets, logger=log,
                        referer=config.referer, verify_tls=config.verify_tls)
    provider = XtreamProvider(config, client, NullCache(), log)
    headers = {"User-Agent": config.user_agent} if config.user_agent else {}
    if config.referer:
        headers["Referer"] = config.referer

    library_dir = os.path.join(args.profile, "library")
    movies_root = os.path.join(library_dir, "movies")
    series_root = os.path.join(library_dir, "series")
    base_url = "plugin://plugin.video.xstreamflex/"

    def on_error(path, exc):
        print("  skipped %s (%s)" % (path, exc))

    def movie_progress(index, total, name):
        sys.stderr.write("\rMovies: category %d/%d: %-50.50s" % (index, total, name))
        sys.stderr.flush()

    def series_progress(index, total, name):
        sys.stderr.write("\rSeries: show %d/%d: %-50.50s" % (index, total, name))
        sys.stderr.flush()

    try:
        all_movies = collect_movies(provider, args.country, progress=movie_progress)
        sys.stderr.write("\n")
        movies_written, movies_removed = sync_movies(
            movies_root, all_movies, base_url, on_error=on_error, headers=headers)

        shows = collect_shows_with_episodes(provider, args.country, progress=series_progress)
        sys.stderr.write("\n")
        series_written, series_removed = sync_episodes(
            series_root, shows, base_url, on_error=on_error, headers=headers)
    finally:
        client.close()

    write_sync_state(args.profile, config.id, movies_written=movies_written,
                     movies_removed=movies_removed, series_written=series_written,
                     series_removed=series_removed)
    print("Movies: +%d/-%d. Series: +%d/-%d." % (
        movies_written, movies_removed, series_written, series_removed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
