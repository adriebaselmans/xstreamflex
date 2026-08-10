#!/usr/bin/env python3
"""Run a real export without Kodi.

The strongest verification available on a development machine: it drives the exact
``core/`` code the add-on uses, against a real account, and writes a playlist you can
inspect line by line.

    python3 tools/export_cli.py --base http://host:8080 --user NAME --out /tmp/ch.m3u
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.xstreamflex", "resources", "lib"))

from core.cache import Cache, NullCache  # noqa: E402
from core.config import KIND_M3U, KIND_XTREAM, ProviderConfig  # noqa: E402
from core.diagnostics import run_diagnostics  # noqa: E402
from core.errors import ProviderError  # noqa: E402
from core.http import DEFAULT_USER_AGENT, HttpClient, Scrubber  # noqa: E402
from core.providers.m3u import M3UProvider  # noqa: E402
from core.providers.xtream import XtreamProvider  # noqa: E402


def make_logger(verbose: bool):
    def log(level: str, message: str) -> None:
        if level == "debug" and not verbose:
            return
        print("[%s] %s" % (level, message), file=sys.stderr)
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", help="Server URL including port, e.g. http://host:8080")
    parser.add_argument("--user", help="Username")
    parser.add_argument("--password-stdin", action="store_true",
                        help="Read the password from stdin instead of prompting")
    parser.add_argument("--m3u", help="Use a plain M3U URL or path instead of the Xtream API")
    parser.add_argument("--out", default="channels.m3u", help="Output playlist path")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--format", default="ts", choices=["ts", "m3u8"])
    parser.add_argument("--limit", type=int, default=0,
                        help="Export only the first N categories (for quick iteration)")
    parser.add_argument("--cache", default="", help="Optional cache database path")
    parser.add_argument("--diagnostics", action="store_true", help="Probe the provider first")
    parser.add_argument("--verbose", action="store_true", help="Log every request")
    args = parser.parse_args()

    if args.m3u:
        config = ProviderConfig(label="cli", kind=KIND_M3U, m3u_url=args.m3u,
                                user_agent=args.user_agent)
    else:
        if not args.base or not args.user:
            parser.error("--base and --user are required unless --m3u is given")
        # Deliberately no --password flag: an argument lands in shell history and in
        # the process table, where any local user can read it with ps.
        password = (sys.stdin.readline().rstrip("\n") if args.password_stdin
                    else getpass.getpass("Password: "))
        config = ProviderConfig(
            label="cli", kind=KIND_XTREAM, base_url=args.base, username=args.user,
            password=password, user_agent=args.user_agent, preferred_format=args.format,
        )

    log = make_logger(args.verbose)
    client = HttpClient(config.user_agent, secrets=config.secrets, logger=log)
    cache = Cache(args.cache) if args.cache else NullCache()

    if config.kind == KIND_M3U:
        provider = M3UProvider(config, client, cache, log)
    else:
        provider = XtreamProvider(config, client, cache, log)

    try:
        if args.diagnostics:
            print(run_diagnostics(config, client).as_text())
            print()

        if config.kind == KIND_XTREAM:
            account = provider.account()
            print("Account: %s, max_connections=%d, formats=%s"
                  % (account.status, account.max_connections,
                     ", ".join(account.allowed_output_formats)))
            categories = provider.categories("live")
            print("Live categories: %d" % len(categories))
            if args.limit:
                categories = categories[: args.limit]
                print("Limiting to the first %d" % len(categories))
            channels = provider.iter_channels(categories=categories, progress=_progress)
        else:
            channels = provider.iter_channels()

        from core.export.m3u_writer import write_m3u
        if config.kind == KIND_XTREAM:
            url_for = lambda channel: provider.live_url(channel.id)  # noqa: E731
        else:
            url_for = lambda channel: channel.direct_source  # noqa: E731

        result = write_m3u(args.out, channels, url_for,
                           user_agent=config.user_agent, referer=config.referer)
    except ProviderError as exc:
        print("\nFailed: %s" % exc.message, file=sys.stderr)
        if exc.detail:
            print("  %s" % exc.detail, file=sys.stderr)
        return 1
    finally:
        client.close()

    print("\nWrote %s: %s (%d bytes)" % (result.path, result.summary(), result.bytes_written))
    for skipped in result.skipped[:5]:
        print("  skipped: %s" % skipped)

    # The playlist URLs embed the account password, so the preview is scrubbed the
    # same way the logger is. The file on disk necessarily still contains them.
    print("\nFirst entries (credentials masked):")
    scrub = Scrubber(config.secrets + ([config.username] if config.username else []))
    with open(result.path, "r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= 12:
                break
            print("  " + scrub(line.rstrip()))
    return 0


def _progress(index: int, total: int, name: str) -> None:
    sys.stderr.write("\r  category %d/%d: %-40.40s" % (index, total, name))
    sys.stderr.flush()


if __name__ == "__main__":
    raise SystemExit(main())
