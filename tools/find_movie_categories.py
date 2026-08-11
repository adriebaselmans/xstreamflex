#!/usr/bin/env python3
"""Find every VOD category a movie title appears in, for a real account.

Diagnostic for "the same movie shows up N times in Kodi's Movies library":
library_sync.py deliberately writes one .strm/.nfo pair per (category, movie)
pair it collects (see collect_movies in core/library_sync.py) so a title
offered under two genuinely different apps -- "NL | Netflix" and
"NL | Videoland" -- shows up under both. If a provider instead scatters the
same title across many overlapping category listings (e.g. "NL | Nieuw",
"NL | Actie", "NL | Films 2026", ...) that same logic produces one duplicate
per overlapping category instead.

    python3 tools/find_movie_categories.py --base http://host:8080 --user NAME \\
        --country NL --title "Formule 1"
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.xstreamflex", "resources", "lib"))

from core.cache import NullCache  # noqa: E402
from core.config import KIND_XTREAM, ProviderConfig  # noqa: E402
from core.http import DEFAULT_USER_AGENT, HttpClient  # noqa: E402
from core.library_sync import matches_country  # noqa: E402
from core.models import VOD  # noqa: E402
from core.providers.xtream import XtreamProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="Server URL including port")
    parser.add_argument("--user", required=True, help="Username")
    parser.add_argument("--password-stdin", action="store_true",
                        help="Read the password from stdin instead of prompting")
    parser.add_argument("--country", default="NL", help="Category-name prefix filter")
    parser.add_argument("--title", required=True, help="Substring to match against movie names")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args()

    password = (sys.stdin.readline().rstrip("\n") if args.password_stdin
                else getpass.getpass("Password: "))
    config = ProviderConfig(label="cli", kind=KIND_XTREAM, base_url=args.base,
                            username=args.user, password=password, user_agent=args.user_agent)
    log = lambda level, message: None  # noqa: E731
    client = HttpClient(config.user_agent, secrets=config.secrets, logger=log)
    provider = XtreamProvider(config, client, NullCache(), log)

    needle = args.title.casefold()
    try:
        categories = [c for c in provider.categories(VOD) if matches_country(c.name, args.country)]
        print("Scanning %d categories matching country=%r ...\n" % (len(categories), args.country))
        hits = []
        for index, category in enumerate(categories, 1):
            sys.stderr.write("\r  %d/%d: %-50.50s" % (index, len(categories), category.name))
            sys.stderr.flush()
            for movie in provider.movies(category.id):
                if needle in movie.name.casefold():
                    hits.append((category.name, movie.id, movie.name))
        sys.stderr.write("\n\n")
    finally:
        client.close()

    if not hits:
        print("No movie matching %r found in any %s category." % (args.title, args.country))
        return 0

    print("Found %d match(es) across %d categor(y/ies):\n"
          % (len(hits), len({c for c, _, _ in hits})))
    for category_name, movie_id, movie_name in hits:
        print("  [%s] id=%s  %s" % (category_name, movie_id, movie_name))

    ids = {movie_id for _, movie_id, _ in hits}
    if len(ids) == 1 and len(hits) > 1:
        print("\n-> Same movie id (%s) listed under %d different categories: this is why it "
              "shows up %d times in Kodi's Movies library." % (next(iter(ids)), len(hits), len(hits)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
