"""Exercises the Kodi UI layer against stub modules.

Its purpose is to catch the mistakes that otherwise only surface as an empty
directory listing after a Kodi restart: import errors, bad routing, wrong argument
counts, and exceptions escaping a handler.
"""
import os

import pytest

import kodistubs

kodistubs.install()

from conftest import FakeClient, load_fixture  # noqa: E402

from core.config import ProviderConfig  # noqa: E402
from core.errors import EndpointDisabledError  # noqa: E402
from core.models import Movie, StreamRef  # noqa: E402
from kodiui import listing, play, router  # noqa: E402
from kodiui.context import Context  # noqa: E402


@pytest.fixture(autouse=True)
def clean(tmp_path):
    kodistubs.reset()
    kodistubs.Addon.info = {"profile": str(tmp_path / "profile")}
    kodistubs.Addon.settings = {}
    kodistubs.Dialog.inputs = []
    kodistubs.Dialog.selects = []
    kodistubs.Dialog.yesnos = []
    kodistubs.Player.playing = False
    yield


def make_context(tmp_path, with_provider=True, responses=None):
    context = Context(handle=1, base_url="plugin://plugin.video.xstreamflex/")
    if with_provider:
        config = ProviderConfig(label="Test", base_url="http://host:8080",
                                username="u", password="p", user_agent="UA/1.0")
        context.store.upsert(config)
        client = FakeClient(responses or {})
        context.provider = lambda cfg=None: (
            _provider(cfg or config, client, context), cfg or config
        )
    return context


def _provider(config, client, context):
    from core.providers.xtream import XtreamProvider
    return XtreamProvider(config, client, context.cache, context.log)


def labels():
    return [item[1].label for item in kodistubs.directory_items
            if not isinstance(item[0], str) or item[0].startswith("plugin://")]


def test_root_without_provider_offers_setup(tmp_path):
    context = make_context(tmp_path, with_provider=False)
    router.dispatch(context, "")

    assert any("Add a provider" in label for label in labels())
    assert ("end", True) in kodistubs.directory_items


def test_root_with_provider_lists_livetv_movies_series(tmp_path):
    context = make_context(tmp_path)
    router.dispatch(context, "?action=root")

    joined = " ".join(labels())
    assert "Live TV" in joined
    assert "Movies" in joined
    assert "Series" in joined


def test_categories_are_listed(tmp_path):
    context = make_context(tmp_path, responses={
        "get_vod_categories": [{"category_id": "9", "category_name": "Action"}],
    })
    router.dispatch(context, "?action=categories&kind=vod")

    assert "Action" in labels()


def test_sync_library_writes_strm_files_for_matching_categories_only(tmp_path):
    context = make_context(tmp_path, responses={
        "get_vod_categories": [
            {"category_id": "9", "category_name": "NL | Filmclub"},
            {"category_id": "8", "category_name": "BE | Cinemax"},
        ],
        "get_vod_streams": load_fixture("vod_streams.json"),
        "get_series_categories": [],
    })

    router.dispatch(context, "?action=sync_library&country=NL")

    movies_dir = os.path.join(context.library_dir, "movies")
    files = [f for f in os.listdir(movies_dir) if f.endswith(".strm")]
    assert len(files) == 1
    with open(os.path.join(movies_dir, files[0]), encoding="utf-8") as handle:
        content = handle.read()
    assert "action=play_movie" in content and "movie_id=555" in content


def test_sync_library_writes_episodes_under_a_show_folder(tmp_path):
    context = make_context(tmp_path, responses={
        "get_vod_categories": [],
        "get_series_categories": [{"category_id": "1", "category_name": "NL | Series"}],
        "get_series": [{"series_id": 42, "name": "Some Show", "category_id": "1"}],
        "get_series_info": load_fixture("series_info.json"),
    })

    router.dispatch(context, "?action=sync_library&country=NL")

    series_dir = os.path.join(context.library_dir, "series")
    show_dirs = os.listdir(series_dir)
    assert len(show_dirs) == 1
    episode_files = [f for f in os.listdir(os.path.join(series_dir, show_dirs[0]))
                     if f.endswith(".strm")]
    assert episode_files
    with open(os.path.join(series_dir, show_dirs[0], episode_files[0]), encoding="utf-8") as handle:
        assert "action=play_episode" in handle.read()


def test_categories_can_be_filtered_by_country_prefix(tmp_path):
    context = make_context(tmp_path, responses={
        "get_vod_categories": [
            {"category_id": "1", "category_name": "NL | Filmclub 2026"},
            {"category_id": "2", "category_name": "nl-Netflix"},
            {"category_id": "3", "category_name": "BE | Cinemax"},
            {"category_id": "4", "category_name": "UK | HD"},
        ],
    })
    router.dispatch(context, "?action=categories&kind=vod&country=NL")

    joined = " ".join(labels())
    assert "Filmclub" in joined and "Netflix" in joined
    assert "Cinemax" not in joined and "HD" not in joined


def test_categories_without_a_matching_country_notify_instead_of_a_blank_list(tmp_path):
    context = make_context(tmp_path, responses={
        "get_vod_categories": [{"category_id": "3", "category_name": "BE | Cinemax"}],
    })
    router.dispatch(context, "?action=categories&kind=vod&country=NL")

    assert kodistubs.notifications
    assert "NL" in kodistubs.notifications[0][1]


def test_movies_are_listed_as_playable(tmp_path):
    context = make_context(tmp_path, responses={
        "get_vod_streams": load_fixture("vod_streams.json"),
    })
    router.dispatch(context, "?action=items&kind=vod&category_id=9")

    entries = [i for i in kodistubs.directory_items if not isinstance(i[0], str) or "plugin://" in i[0]]
    url, item, is_folder = entries[0]
    assert is_folder is False
    assert item.getProperty("IsPlayable") == "true"
    assert "play_movie" in url
    assert item._tag.values["mediatype"] == "movie"


def test_seasons_collapse_to_episodes_for_single_season(tmp_path):
    payload = load_fixture("series_info.json")
    payload["episodes"].pop("2")
    context = make_context(tmp_path, responses={"get_series_info": payload})

    router.dispatch(context, "?action=seasons&series_id=42")
    assert any("Pilot" in label for label in labels())


def test_seasons_are_listed_when_there_are_several(tmp_path):
    context = make_context(tmp_path, responses={
        "get_series_info": load_fixture("series_info.json"),
    })
    router.dispatch(context, "?action=seasons&series_id=42")

    assert "Season 1" in labels() and "Season 2" in labels()


def test_play_channel_resolves_with_headers(tmp_path):
    context = make_context(tmp_path)
    router.dispatch(context, "?action=play_channel&channel_id=843174&title=NPO1")

    succeeded, item = kodistubs.resolved[0]
    assert succeeded is True
    assert item.path.startswith("http://host:8080/live/u/p/843174.ts|")
    assert "User-Agent=UA%2F1.0" in item.path
    assert item.mimetype == "video/mp2t"


def test_play_movie_uses_the_proxy_when_available(tmp_path, monkeypatch):
    proxied = StreamRef(url="http://127.0.0.1:19191/stream/tok/1.mkv", headers={})
    monkeypatch.setattr(play, "proxied_ref", lambda ref: proxied)
    context = make_context(tmp_path)

    router.dispatch(context, "?action=play_movie&movie_id=1&ext=mkv&title=x")

    succeeded, item = kodistubs.resolved[0]
    assert succeeded is True
    assert item.path.startswith("http://127.0.0.1:19191/stream/tok/1.mkv")


def test_play_movie_falls_back_to_the_direct_url_when_the_proxy_is_unreachable(tmp_path, monkeypatch):
    """core.proxy.client_register returns None when service.py's proxy isn't
    reachable; playback must still work exactly as it did before the proxy."""
    monkeypatch.setattr(play, "proxied_ref", lambda ref: None)
    context = make_context(tmp_path)

    router.dispatch(context, "?action=play_movie&movie_id=1&ext=mkv&title=x")

    succeeded, item = kodistubs.resolved[0]
    assert succeeded is True
    assert "127.0.0.1" not in item.path
    assert item.path.startswith("http://host:8080/movie/u/p/1.mkv")


def test_play_channel_is_not_proxied(tmp_path, monkeypatch):
    """Live TV plays through Kodi's PVR section in normal use; this action
    exists but proxying it is out of scope for the VOD failures this fixes."""
    def boom(ref):
        raise AssertionError("live playback must not go through the proxy")
    monkeypatch.setattr(play, "proxied_ref", boom)
    context = make_context(tmp_path)

    router.dispatch(context, "?action=play_channel&channel_id=843174&title=NPO1")

    assert kodistubs.resolved[0][0] is True


def test_play_stops_existing_playback_on_single_connection_accounts(tmp_path):
    kodistubs.Player.playing = True
    context = make_context(tmp_path)
    router.dispatch(context, "?action=play_channel&channel_id=1&title=x")

    assert kodistubs.Player.playing is False


def test_provider_error_during_playback_fails_the_resolve(tmp_path):
    context = make_context(tmp_path, responses={
        "get_vod_streams": EndpointDisabledError("Provider has disabled this endpoint (885)."),
    })
    router.dispatch(context, "?action=items&kind=vod&category_id=9")

    assert kodistubs.notifications
    assert "885" in kodistubs.notifications[0][1]
    assert ("end", False) in kodistubs.directory_items


def test_unknown_action_falls_back_to_root(tmp_path):
    context = make_context(tmp_path)
    router.dispatch(context, "?action=does_not_exist")

    assert any("Live TV" in label for label in labels())


def test_export_writes_a_playlist(tmp_path):
    context = make_context(tmp_path, responses={
        "get_live_categories": load_fixture("live_categories.json"),
        "get_live_streams": load_fixture("live_streams.json"),
    })
    router.dispatch(context, "?action=export")

    config = context.store.active()
    path = context.playlist_path(config)
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert text.startswith("#EXTM3U")
    assert '#EXTVLCOPT:http-user-agent="UA/1.0"' in text
    assert "http://host:8080/live/u/p/843174.ts" in text
    assert kodistubs.notifications


def test_iptvsimple_setup_shows_values_when_it_cannot_apply(tmp_path):
    context = make_context(tmp_path)
    router.dispatch(context, "?action=iptvsimple_setup")

    heading, text = kodistubs.textviewers[0]
    assert "IPTV Simple" in heading
    assert "channels-" in text
    assert "xmltv.php" in text


def test_show_paths_lists_the_playlist_location(tmp_path):
    context = make_context(tmp_path)
    router.dispatch(context, "?action=show_paths")

    _, text = kodistubs.textviewers[0]
    assert context.export_dir in text


def test_provider_add_saves_and_activates(tmp_path):
    context = make_context(tmp_path, with_provider=False)
    kodistubs.Dialog.selects = [0]  # Xtream
    kodistubs.Dialog.inputs = ["My provider", "host:8080", "user", "pass", "UA/9"]

    router.dispatch(context, "?action=provider_add")

    active = context.store.active()
    assert active.label == "My provider"
    assert active.base_url == "http://host:8080"
    assert active.user_agent == "UA/9"


def test_provider_add_cancelled_saves_nothing(tmp_path):
    context = make_context(tmp_path, with_provider=False)
    kodistubs.Dialog.selects = [-1]

    router.dispatch(context, "?action=provider_add")
    assert context.store.all() == []


def test_all_routed_actions_are_callable(tmp_path, monkeypatch):
    """Every registered action must run to completion without hitting the guard.

    Asserting only that the directory was closed proves nothing: ``dispatch``
    catches everything and ``_abort`` closes the directory too, so a crash would
    look identical. Record what reaches ``_abort`` instead.
    """
    context = make_context(tmp_path, responses={
        "": load_fixture("account.json"),
        "get_live_categories": [],
        "get_vod_categories": [],
        "get_series_categories": [],
        "get_live_streams": [],
        "get_vod_streams": [],
        "get_series": [],
        "get_series_info": load_fixture("series_info.json"),
    })

    aborted = {}
    original = router._abort

    def record(ctx, params, message):
        aborted[params.get("action", "")] = message
        return original(ctx, params, message)

    monkeypatch.setattr(router, "_abort", record)

    for action in sorted(router._HANDLERS):
        kodistubs.reset()
        kodistubs.Dialog.selects = [-1]
        router.dispatch(context, "?action=%s" % action)
        assert kodistubs.directory_items or kodistubs.resolved, action

    # provider_edit legitimately aborts: the fixture has no such provider id.
    assert set(aborted) == {"provider_edit"}, aborted


def test_the_route_guard_reports_a_crashing_handler(tmp_path):
    """Proves the guard above actually detects a broken handler."""
    context = make_context(tmp_path)

    @router.route("_test_boom")
    def boom(ctx, params):
        raise RuntimeError("kaboom")

    try:
        router.dispatch(context, "?action=_test_boom")
        assert kodistubs.notifications
        assert ("end", False) in kodistubs.directory_items
        assert any("kaboom" in message for _, message in kodistubs.log_lines)
    finally:
        del router._HANDLERS["_test_boom"]


# -- VOD playback uses ffmpegdirect for its real HTTP reconnect, not realtime mode

def test_vod_playback_uses_ffmpegdirect_without_realtime_mode():
    """A movie/episode file is static; ffmpegdirect's timeshift mode is for a
    continuously growing live buffer and fails or refuses to seek on a VOD file.
    Plain (non-realtime) ffmpegdirect is still used, for ffmpeg's own HTTP
    reconnect — Kodi's native player retries a failed open exactly once, about
    35ms later, which is not enough to ride out a provider's transient blip."""
    kodistubs.Addon.installed.add("inputstream.ffmpegdirect")
    try:
        ref = StreamRef(url="http://host/movie/u/p/1.mp4", headers={"User-Agent": "UA"})
        item = play.build_list_item(ref, "Some Movie")
        assert item.getProperty("inputstream") == "inputstream.ffmpegdirect"
        assert item.getProperty("inputstream.ffmpegdirect.is_realtime_stream") == "false"
        assert item.getProperty("inputstream.ffmpegdirect.stream_mode") == ""
        assert item.getProperty("inputstream.ffmpegdirect.open_mode") == "ffmpeg"
        assert "reconnect=1" in item.path
        assert "reconnect_streamed=1" in item.path
        assert "reconnect_at_eof" not in item.path
    finally:
        kodistubs.Addon.installed.discard("inputstream.ffmpegdirect")


def test_vod_playback_falls_back_to_kodis_default_player_without_ffmpegdirect():
    ref = StreamRef(url="http://host/movie/u/p/1.mp4", headers={"User-Agent": "UA"})
    item = play.build_list_item(ref, "Some Movie")
    assert item.getProperty("inputstream") == ""
    assert "reconnect" not in item.path


def test_live_ts_playback_still_uses_ffmpegdirect_timeshift_when_available():
    kodistubs.Addon.installed.add("inputstream.ffmpegdirect")
    try:
        ref = StreamRef(url="http://host/live/u/p/1.ts", headers={"User-Agent": "UA"},
                        live=True)
        item = play.build_list_item(ref, "Some Channel")
        assert item.getProperty("inputstream") == "inputstream.ffmpegdirect"
        assert item.getProperty("inputstream.ffmpegdirect.is_realtime_stream") == "true"
        assert item.getProperty("inputstream.ffmpegdirect.stream_mode") == "timeshift"
        assert item.getProperty("inputstream.ffmpegdirect.open_mode") == "ffmpeg"
        assert "reconnect_at_eof=1" in item.path
    finally:
        kodistubs.Addon.installed.discard("inputstream.ffmpegdirect")


# -- Artwork needs the provider's User-Agent just as much as playback does --

def test_movie_poster_carries_the_required_user_agent():
    movie = Movie(id="1", name="Some Movie", icon="http://img/poster.jpg")
    _, item, _ = listing.movie_item("plugin://x/", movie, {"User-Agent": "UA/1.0"})
    assert item.art["poster"] == "http://img/poster.jpg|User-Agent=UA%2F1.0"
    assert item.art["thumb"] == item.art["poster"]


def test_movie_poster_without_headers_is_unchanged():
    movie = Movie(id="1", name="Some Movie", icon="http://img/poster.jpg")
    _, item, _ = listing.movie_item("plugin://x/", movie)
    assert item.art["poster"] == "http://img/poster.jpg"
