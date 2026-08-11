"""Background export scheduler.

The only unattended component, so it stays small: check staleness, export, log,
sleep. It skips work while something is playing, because an account limited to one
connection cannot afford an API sweep during playback.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

import xbmc  # noqa: E402

from core.errors import ProviderError  # noqa: E402
from core.export.exporter import export_channels, is_stale  # noqa: E402
from core.library_sync import (  # noqa: E402
    collect_movies,
    collect_shows_with_episodes,
    is_sync_stale,
    sync_episodes,
    sync_movies,
    write_sync_state,
)
from core.proxy import ProxyServer  # noqa: E402
from kodiui.context import Context  # noqa: E402

CHECK_INTERVAL_SECONDS = 300


def _run_library_sync(context: Context) -> None:
    context.reload()
    config = context.store.active()
    if config is None or not config.is_complete:
        return

    interval_hours = max(1, context.setting_int("export_interval_hours", 6))
    if not is_sync_stale(context.profile, config.id, interval_hours * 3600):
        return

    if xbmc.Player().isPlaying():
        context.log("debug", "library sync postponed: playback in progress")
        return

    country = context.setting("library_country", "NL")

    def on_error(path, exc):
        context.log("warning", "library sync: skipped %s (%s)" % (path, exc))

    # Poster/thumb URLs need the same header this provider requires for every
    # other request (see PROVIDER-FINDINGS.md); Kodi fetches NFO-referenced art
    # itself, outside any hand-off this add-on controls otherwise.
    headers = {"User-Agent": config.user_agent} if config.user_agent else {}
    if config.referer:
        headers["Referer"] = config.referer

    try:
        provider, _ = context.provider(config)
        if provider is None:
            return
        movies_root = os.path.join(context.library_dir, "movies")
        series_root = os.path.join(context.library_dir, "series")
        movies_written, movies_removed = sync_movies(
            movies_root, collect_movies(provider, country), context.base_url,
            on_error=on_error, headers=headers)
        shows = collect_shows_with_episodes(provider, country)
        series_written, series_removed = sync_episodes(
            series_root, shows, context.base_url, on_error=on_error, headers=headers)
    except ProviderError as exc:
        context.log("warning", "scheduled library sync failed: %s" % exc.message)
        return
    except Exception as exc:
        context.log("error", "scheduled library sync crashed: %s" % exc)
        return

    write_sync_state(context.profile, config.id, movies_written=movies_written,
                     movies_removed=movies_removed, series_written=series_written,
                     series_removed=series_removed)
    context.log("info", "scheduled library sync: movies +%d/-%d, series +%d/-%d"
                % (movies_written, movies_removed, series_written, series_removed))

    # Kodi's own library scanner has to actually run for new/removed .strm files
    # to be reflected in the Movies/TV Shows sections - it does not watch the
    # filesystem. xbmc.executeJSONRPC talks to Kodi's JSON-RPC in-process, so
    # this works whether or not the HTTP webserver (Settings > Services) is on.
    if movies_written or movies_removed:
        _scan_library(context, movies_root)
    if series_written or series_removed:
        _scan_library(context, series_root)


def _scan_library(context: Context, directory: str) -> None:
    # VideoLibrary.Scan (raw JSON-RPC) was tried first: it "succeeded" (no
    # error in the response) but the resulting scan aborted after a single
    # item, seconds after this process had just finished writing tens of
    # thousands of files - almost certainly a race with Kodi's own directory
    # cache. UpdateLibrary is the exact built-in action Kodi's own "Update
    # library" menu item runs, confirmed working manually on the same
    # machine/timing, so use that instead of a lower-level API that behaves
    # differently for reasons this add-on does not control.
    xbmc.executebuiltin('UpdateLibrary("video", "%s")' % directory)


def _run_export(context: Context) -> None:
    # A provider may have been added through the plugin UI since the last tick, in a
    # different interpreter.
    context.reload()
    config = context.store.active()
    if config is None or not config.is_complete:
        return

    interval_hours = max(1, context.setting_int("export_interval_hours", 6))
    if not is_stale(context.export_dir, config.id, interval_hours * 3600):
        return

    if xbmc.Player().isPlaying():
        context.log("debug", "export postponed: playback in progress")
        return

    path = context.playlist_path(config)
    # Building the provider opens a session and reads settings, so it belongs inside
    # the guard: an exception escaping here would end the service thread for the
    # rest of the Kodi session.
    try:
        provider, _ = context.provider(config)
        if provider is None:
            return
        result = export_channels(provider, config, path)
    except ProviderError as exc:
        context.log("warning", "scheduled export failed: %s" % exc.message)
        return
    except Exception as exc:
        context.log("error", "scheduled export crashed: %s" % exc)
        return
    context.log("info", "scheduled export: %s -> %s" % (result.summary(), path))


def main() -> None:
    context = Context()
    monitor = xbmc.Monitor()
    context.log("info", "service started")

    # Each plugin:// invocation Kodi makes is its own short-lived interpreter and
    # cannot host a server spanning the length of a movie; this process is the
    # only long-lived one, so playback's local proxy (core.proxy) lives here.
    proxy = ProxyServer(logger=context.log)
    proxy.start()

    if context.setting_bool("export_on_startup", True) and context.setting_bool(
        "export_enabled", True
    ):
        # Kodi is busy starting up; a short delay keeps the UI responsive and gives
        # the network stack time to come up on set-top boxes.
        if not monitor.waitForAbort(30):
            _run_export(context)
            if context.setting_bool("library_sync_enabled", True):
                _run_library_sync(context)

    while not monitor.abortRequested():
        if monitor.waitForAbort(CHECK_INTERVAL_SECONDS):
            break
        if context.setting_bool("export_enabled", True):
            _run_export(context)
        if context.setting_bool("library_sync_enabled", True):
            _run_library_sync(context)

    proxy.stop()
    context.log("info", "service stopped")


if __name__ == "__main__":
    main()
