"""The background export scheduler.

Untested in the first draft, which is how a provider added after Kodi started could
be invisible to the service until a restart.
"""
import importlib
import os
import sys

import pytest

import kodistubs

kodistubs.install()

from conftest import FakeClient, load_fixture  # noqa: E402

from core.config import ProviderConfig  # noqa: E402
from core.errors import TransientError  # noqa: E402
from core.export.exporter import _write_state  # noqa: E402
from core.models import ExportResult  # noqa: E402
from kodiui.context import Context  # noqa: E402

ADDON_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin.video.xstreamflex"
)


@pytest.fixture
def service(tmp_path):
    kodistubs.reset()
    kodistubs.Addon.info = {"profile": str(tmp_path / "profile")}
    kodistubs.Addon.settings = {}
    kodistubs.Player.playing = False
    if ADDON_ROOT not in sys.path:
        sys.path.insert(0, ADDON_ROOT)
    module = importlib.import_module("service")
    return importlib.reload(module)


def context_with_provider(responses=None):
    context = Context()
    config = ProviderConfig(label="T", base_url="http://host:8080",
                            username="u", password="p", user_agent="UA/1.0")
    context.store.upsert(config)
    client = FakeClient(responses or {
        "get_live_categories": load_fixture("live_categories.json"),
        "get_live_streams": load_fixture("live_streams.json"),
    })

    from core.providers.xtream import XtreamProvider
    context.provider = lambda cfg=None: (
        XtreamProvider(cfg or config, client, context.cache, context.log), cfg or config
    )
    return context, config


def test_export_runs_when_state_is_missing(service, tmp_path):
    context, config = context_with_provider()
    service._run_export(context)

    assert os.path.exists(context.playlist_path(config))


def test_export_is_skipped_while_something_is_playing(service, tmp_path):
    context, config = context_with_provider()
    kodistubs.Player.playing = True

    service._run_export(context)

    assert not os.path.exists(context.playlist_path(config))


def test_export_is_skipped_when_the_state_is_fresh(service, tmp_path):
    context, config = context_with_provider()
    _write_state(context.export_dir, config.id, ExportResult(path="x", channel_count=1))

    service._run_export(context)
    assert not os.path.exists(context.playlist_path(config))


def test_provider_added_after_startup_is_picked_up(service, tmp_path):
    """The service holds one Context for the whole Kodi session."""
    context = Context()
    assert context.store.active() is None
    service._run_export(context)  # nothing configured yet, must not raise

    # A second process (addon.py) adds a provider to the same file.
    other = Context()
    config = ProviderConfig(label="T", base_url="http://host:8080",
                            username="u", password="p")
    other.store.upsert(config)

    from core.providers.xtream import XtreamProvider
    client = FakeClient({
        "get_live_categories": load_fixture("live_categories.json"),
        "get_live_streams": load_fixture("live_streams.json"),
    })
    context.provider = lambda cfg=None: (
        XtreamProvider(cfg or config, client, context.cache, context.log), cfg or config
    )

    service._run_export(context)
    assert os.path.exists(context.playlist_path(config))


def test_provider_failure_does_not_kill_the_service(service, tmp_path):
    context, config = context_with_provider(responses={
        "get_live_categories": TransientError("Provider did not answer in time."),
    })

    service._run_export(context)  # must return, not raise

    assert any("scheduled export failed" in message
               for _, message in kodistubs.log_lines)


def test_unexpected_error_does_not_kill_the_service(service, tmp_path):
    context, _ = context_with_provider()

    def explode(*args, **kwargs):
        raise ValueError("something odd")

    context.provider = explode
    service._run_export(context)

    assert any("crashed" in message for _, message in kodistubs.log_lines)
