"""plugin:// dispatch.

Kodi swallows exceptions raised inside a plugin call and shows an empty directory
with no explanation, so every handler runs inside a guard that logs the traceback
first.
"""
from __future__ import annotations

import traceback
from typing import Callable, Dict
from urllib.parse import parse_qsl

import xbmcgui
import xbmcplugin

from core.diagnostics import run_diagnostics
from core.errors import NotSupportedError, ProviderError
from core.export.exporter import export_channels, last_export_time
from core.export.iptvsimple import apply_plan, build_plan
from core.models import SERIES, VOD, Episode
from . import dialogs, listing, play
from .context import Context

_HANDLERS: Dict[str, Callable] = {}


def route(action: str):
    def decorator(func):
        _HANDLERS[action] = func
        return func
    return decorator


def dispatch(context: Context, query_string: str) -> None:
    params = dict(parse_qsl(query_string.lstrip("?")))
    action = params.get("action", "root")
    handler = _HANDLERS.get(action)
    if handler is None:
        context.log("warning", "unknown action %r" % action)
        handler = _HANDLERS["root"]
    try:
        handler(context, params)
    except NotSupportedError as exc:
        _abort(context, params, str(exc))
    except ProviderError as exc:
        context.log("error", "%s: %s | %s" % (action, exc.message, exc.detail))
        _abort(context, params, exc.message)
    except Exception as exc:  # pragma: no cover - last resort
        context.log("error", "unhandled error in %s: %s\n%s"
                    % (action, exc, traceback.format_exc()))
        _abort(context, params, "Something went wrong. See the Kodi log for details.")


def _abort(context: Context, params: Dict[str, str], message: str) -> None:
    """End the call cleanly so Kodi does not leave a spinner or a blank list."""
    if params.get("action", "").startswith("play_"):
        play.fail(context.handle, message)
        return
    dialogs.notify(message, error=True)
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=False)


def _url(context: Context, action: str, **params) -> str:
    return listing.url_for(context.base_url, action, **params)


def _require_provider(context: Context):
    provider, config = context.provider()
    if provider is None or config is None or not config.is_complete:
        raise NotSupportedError("No provider is configured yet. Add one first.")
    return provider, config


# -- menus ---------------------------------------------------------------

@route("root")
def root(context: Context, params) -> None:
    provider_config = context.store.active()
    items = []

    if provider_config is None:
        items.append(listing.folder_item(
            "Add a provider", _url(context, "provider_add"),
            plot="Set up your Xtream account or M3U playlist to get started.",
        ))
    else:
        exported = last_export_time(context.export_dir, provider_config.id)
        status = "never exported" if not exported else "last export: %s" % _ago(exported)
        items.append(listing.folder_item(
            "Live TV  ·  %s" % status, _url(context, "livetv"),
            plot=("Live TV plays through Kodi's own TV section. This menu manages the "
                  "channel list that feeds it."),
        ))
        provider, _ = context.provider(provider_config)
        if provider is not None and provider.capabilities.vod:
            items.append(listing.folder_item(
                "Movies", _url(context, "categories", kind=VOD)))
        if provider is not None and provider.capabilities.series:
            items.append(listing.folder_item(
                "Series", _url(context, "categories", kind=SERIES)))

    items.append(listing.folder_item(
        "Providers%s" % (" (%s)" % provider_config.label if provider_config else ""),
        _url(context, "providers"),
    ))
    items.append(listing.folder_item(
        "Diagnostics", _url(context, "diagnostics"),
        plot="Test what your provider accepts and refuses.",
    ))
    items.append(listing.folder_item("Settings", _url(context, "settings")))
    listing.finish(context.handle, items)


@route("livetv")
def livetv(context: Context, params) -> None:
    provider_config = context.store.active()
    if provider_config is None:
        raise NotSupportedError("No provider is configured yet.")
    path = context.playlist_path(provider_config)
    exported = last_export_time(context.export_dir, provider_config.id)

    items = [
        listing.folder_item(
            "Rebuild channel list now", _url(context, "export"),
            plot="Fetch every category from the provider and rewrite %s" % path,
        ),
        listing.folder_item(
            "Set up IPTV Simple", _url(context, "iptvsimple_setup"),
            plot="Point IPTV Simple at the exported playlist and your EPG.",
        ),
        listing.folder_item(
            "Show playlist path", _url(context, "show_paths"),
            plot=path,
        ),
    ]
    if exported:
        items.insert(0, listing.folder_item(
            "Last export: %s" % _ago(exported), _url(context, "noop"), plot=path))
    listing.finish(context.handle, items)


@route("noop")
def noop(context: Context, params) -> None:
    listing.finish(context.handle, [])


@route("categories")
def categories(context: Context, params) -> None:
    kind = params.get("kind", VOD)
    provider, _ = _require_provider(context)
    items = [
        listing.folder_item(
            category.name,
            _url(context, "items", kind=kind, category_id=category.id),
        )
        for category in provider.categories(kind)
    ]
    if not items:
        dialogs.notify("This provider has no %s categories." % kind)
    listing.finish(context.handle, items)


@route("items")
def items(context: Context, params) -> None:
    kind = params.get("kind", VOD)
    category_id = params.get("category_id", "")
    provider, _ = _require_provider(context)

    if kind == VOD:
        entries = [listing.movie_item(context.base_url, movie)
                   for movie in provider.movies(category_id)]
        listing.finish(context.handle, entries, content="movies")
    elif kind == SERIES:
        entries = [listing.series_item(context.base_url, show)
                   for show in provider.series(category_id)]
        listing.finish(context.handle, entries, content="tvshows")
    else:
        entries = [listing.channel_item(context.base_url, channel)
                   for channel in provider.channels(category_id)]
        listing.finish(context.handle, entries)


@route("seasons")
def seasons(context: Context, params) -> None:
    series_id = params.get("series_id", "")
    provider, _ = _require_provider(context)
    show, episodes = provider.series_info(series_id)

    numbers = sorted({episode.season for episode in episodes})
    if len(numbers) <= 1:
        entries = [listing.episode_item(context.base_url, episode, show.name)
                   for episode in episodes]
        listing.finish(context.handle, entries, content="episodes")
        return

    entries = [
        listing.folder_item(
            "Season %d" % number,
            _url(context, "episodes", series_id=series_id, season=str(number)),
            icon=show.cover,
        )
        for number in numbers
    ]
    listing.finish(context.handle, entries, content="seasons")


@route("episodes")
def episodes(context: Context, params) -> None:
    series_id = params.get("series_id", "")
    season = int(params.get("season", "0") or 0)
    provider, _ = _require_provider(context)
    show, all_episodes = provider.series_info(series_id)
    entries = [
        listing.episode_item(context.base_url, episode, show.name)
        for episode in all_episodes if episode.season == season
    ]
    listing.finish(context.handle, entries, content="episodes")


# -- playback -------------------------------------------------------------

@route("play_channel")
def play_channel(context: Context, params) -> None:
    provider, config = _require_provider(context)
    channel_id = params.get("channel_id", "")
    from core.models import Channel
    ref = provider.live_stream(Channel(id=channel_id, name=params.get("title", "")))
    _play(context, ref, params.get("title", ""), config)


@route("play_movie")
def play_movie(context: Context, params) -> None:
    provider, config = _require_provider(context)
    from core.models import Movie
    movie = Movie(id=params.get("movie_id", ""), name=params.get("title", ""),
                  container_extension=params.get("ext", "mp4"))
    _play(context, provider.movie_stream(movie), movie.name, config)


@route("play_episode")
def play_episode(context: Context, params) -> None:
    provider, config = _require_provider(context)
    episode = Episode(
        id=params.get("episode_id", ""), series_id="", season=0, episode=0,
        title=params.get("title", ""), container_extension=params.get("ext", "mp4"),
    )
    _play(context, provider.episode_stream(episode), episode.title, config)


def _play(context: Context, ref, label: str, config) -> None:
    if config.max_connections <= 1:
        # The provider counts the old session for a moment after the player lets go.
        play.stop_current_playback()
    play.resolve(context.handle, ref, label, context.log)


# -- export and IPTV Simple ----------------------------------------------

@route("export")
def export(context: Context, params) -> None:
    provider, config = _require_provider(context)
    path = context.playlist_path(config)

    progress = xbmcgui.DialogProgress()
    progress.create("XstreamFlex", "Fetching channels…")

    def report(index: int, total: int, name: str) -> None:
        if progress.iscanceled():
            raise ProviderError("Export cancelled.")
        progress.update(int(index * 100 / max(1, total)),
                        "Category %d of %d\n%s" % (index, total, name))

    try:
        result = export_channels(provider, config, path, progress=report)
    finally:
        progress.close()

    context.log("info", "export: %s -> %s" % (result.summary(), path))
    dialogs.notify("Exported %s" % result.summary())
    if result.skipped:
        context.log("warning", "skipped %d channels: %s"
                    % (len(result.skipped), "; ".join(result.skipped[:10])))
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=True)


@route("iptvsimple_setup")
def iptvsimple_setup(context: Context, params) -> None:
    config = context.store.active()
    if config is None:
        raise NotSupportedError("No provider is configured yet.")

    path = context.playlist_path(config)
    plan = build_plan(
        path, config.xmltv_url, config.user_agent,
        refresh_minutes=max(5, context.setting_int("export_interval_hours", 6) * 60),
    )
    plan = apply_plan(plan, context.kodi_addon_data)

    lines = ["Playlist: %s" % path, ""]
    if plan.applied:
        lines.insert(0, "IPTV Simple has been configured automatically.")
        lines.append(plan.reason)
        lines.append("")
        lines.append("A backup of its previous settings is next to the original file.")
    else:
        lines.insert(0, "Enter these values in IPTV Simple's settings:")
        lines.append("")
        lines.extend(plan.as_instructions())
        if plan.reason:
            lines += ["", plan.reason]

    dialogs.show_report("IPTV Simple setup", "\n".join(lines))
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=True)


@route("show_paths")
def show_paths(context: Context, params) -> None:
    config = context.store.active()
    lines = [
        "Profile: %s" % context.profile,
        "Export folder: %s" % context.export_dir,
    ]
    if config is not None:
        lines += [
            "Playlist: %s" % context.playlist_path(config),
            "EPG URL: %s" % (config.xmltv_url or "not configured"),
        ]
    dialogs.show_report("Paths", "\n".join(lines))
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=True)


# -- providers ------------------------------------------------------------

@route("providers")
def providers(context: Context, params) -> None:
    active = context.store.active()
    items = [
        listing.folder_item(
            "%s%s" % ("> " if active and provider.id == active.id else "", provider.describe()),
            _url(context, "provider_edit", provider_id=provider.id),
        )
        for provider in context.store.all()
    ]
    items.append(listing.folder_item("Add provider", _url(context, "provider_add")))
    listing.finish(context.handle, items)


@route("provider_add")
def provider_add(context: Context, params) -> None:
    config = dialogs.edit_provider()
    if config is None:
        if context.handle >= 0:
            xbmcplugin.endOfDirectory(context.handle, succeeded=False)
        return
    context.store.upsert(config)
    context.store.set_active(config.id)
    dialogs.notify("Provider saved. Run Diagnostics to verify it.")
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=True)


@route("provider_edit")
def provider_edit(context: Context, params) -> None:
    provider_id = params.get("provider_id", "")
    config = context.store.get(provider_id)
    if config is None:
        raise NotSupportedError("That provider no longer exists.")

    choice = xbmcgui.Dialog().select(config.label, [
        "Make active", "Edit", "Delete",
    ])
    if choice == 0:
        context.store.set_active(provider_id)
        # Cached listings belong to the previous provider and would be misleading.
        context.cache.purge_expired()
        dialogs.notify("%s is now the active provider." % config.label)
    elif choice == 1:
        updated = dialogs.edit_provider(config)
        if updated is not None:
            context.store.upsert(updated)
            context.cache.invalidate("%s:%s" % (updated.kind, updated.id))
            dialogs.notify("Provider updated.")
    elif choice == 2:
        if dialogs.confirm("Delete provider", "Remove %s?" % config.label):
            context.store.remove(provider_id)
            context.cache.invalidate("%s:%s" % (config.kind, config.id))
            dialogs.notify("Provider removed.")
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=True)


@route("diagnostics")
def diagnostics(context: Context, params) -> None:
    config = context.store.active()
    if config is None:
        raise NotSupportedError("No provider is configured yet.")

    progress = xbmcgui.DialogProgress()
    progress.create("XstreamFlex", "Running diagnostics…")

    def report(percent: int, label: str) -> None:
        progress.update(percent, label)

    client = context.http_client(config)
    try:
        result = run_diagnostics(config, client, report)
    finally:
        progress.close()
        client.close()

    context.log("info", "diagnostics: %s" % result.verdict)
    dialogs.show_report("Diagnostics — %s" % result.verdict, result.as_text())
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=True)


@route("settings")
def settings(context: Context, params) -> None:
    context.addon.openSettings()
    if context.handle >= 0:
        xbmcplugin.endOfDirectory(context.handle, succeeded=True)


def _ago(timestamp: int) -> str:
    import time
    delta = max(0, int(time.time() - timestamp))
    if delta < 90:
        return "just now"
    if delta < 5400:
        return "%d minutes ago" % (delta // 60)
    if delta < 172800:
        return "%d hours ago" % (delta // 3600)
    return "%d days ago" % (delta // 86400)
