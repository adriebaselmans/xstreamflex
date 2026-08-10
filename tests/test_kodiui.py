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
from kodiui import router  # noqa: E402
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
    assert "#EXTVLCOPT:http-user-agent=UA/1.0" in text
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


def test_all_routed_actions_are_callable(tmp_path):
    """Every registered action must at least dispatch without an unhandled error."""
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
    for action in sorted(router._HANDLERS):
        kodistubs.reset()
        kodistubs.Dialog.selects = [-1]
        router.dispatch(context, "?action=%s" % action)
        # Either the directory was closed or a stream was resolved.
        assert kodistubs.directory_items or kodistubs.resolved, action
