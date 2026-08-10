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
from kodiui.context import Context  # noqa: E402

CHECK_INTERVAL_SECONDS = 300


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

    if context.setting_bool("export_on_startup", True) and context.setting_bool(
        "export_enabled", True
    ):
        # Kodi is busy starting up; a short delay keeps the UI responsive and gives
        # the network stack time to come up on set-top boxes.
        if not monitor.waitForAbort(30):
            _run_export(context)

    while not monitor.abortRequested():
        if monitor.waitForAbort(CHECK_INTERVAL_SECONDS):
            break
        if context.setting_bool("export_enabled", True):
            _run_export(context)

    context.log("info", "service stopped")


if __name__ == "__main__":
    main()
