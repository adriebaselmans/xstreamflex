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


class FakeMonitor:
    """xbmc.Monitor whose waits are scripted.

    The shared stub aborts on the first wait so that main() exits; the clean has
    to be able to wait more than once, and to see a scan finish while it does.
    """

    def __init__(self, abort_on_call=None, stop_scanning_after=None):
        self.calls = 0
        self.abort_on_call = abort_on_call
        self.stop_scanning_after = stop_scanning_after

    def waitForAbort(self, timeout=0):
        self.calls += 1
        if self.stop_scanning_after is not None and self.calls >= self.stop_scanning_after:
            kodistubs.conditions["Library.IsScanningVideo"] = False
        return self.abort_on_call is not None and self.calls >= self.abort_on_call

    def abortRequested(self):
        return False


def cleans():
    return [command for command in kodistubs.builtins if command.startswith("CleanLibrary")]


def test_clean_runs_when_no_scan_is_in_progress(service):
    context = Context()

    service._clean_library(context, FakeMonitor())

    assert cleans() == ['CleanLibrary("video", false)']


def test_clean_waits_for_a_running_scan_to_finish(service):
    context = Context()
    kodistubs.conditions["Library.IsScanningVideo"] = True

    monitor = FakeMonitor(stop_scanning_after=4)
    service._clean_library(context, monitor)

    assert cleans() == ['CleanLibrary("video", false)']
    assert monitor.calls >= 4, "should have waited rather than cleaned immediately"


def test_clean_gives_up_on_a_scan_that_never_ends(service):
    """A stuck scan must not pin the service thread indefinitely."""
    context = Context()
    kodistubs.conditions["Library.IsScanningVideo"] = True

    service._clean_library(context, FakeMonitor())

    assert cleans() == []
    assert any("library clean skipped" in message for _, message in kodistubs.log_lines)


def test_clean_stops_when_kodi_is_shutting_down(service):
    context = Context()
    kodistubs.conditions["Library.IsScanningVideo"] = True

    service._clean_library(context, FakeMonitor(abort_on_call=2))

    assert cleans() == []


def _stub_sync(service, monkeypatch, movies, series):
    monkeypatch.setattr(service, "is_sync_stale", lambda *a, **k: True)
    monkeypatch.setattr(service, "collect_movies", lambda *a, **k: [])
    monkeypatch.setattr(service, "collect_shows_with_episodes", lambda *a, **k: [])
    monkeypatch.setattr(service, "sync_movies", lambda *a, **k: movies)
    monkeypatch.setattr(service, "sync_episodes", lambda *a, **k: series)
    monkeypatch.setattr(service, "write_sync_state", lambda *a, **k: None)


def test_removed_titles_trigger_a_clean(service, monkeypatch):
    """UpdateLibrary only adds, so a removal that is not cleaned stays visible."""
    context, _ = context_with_provider()
    _stub_sync(service, monkeypatch, movies=(0, 1), series=(0, 0))

    service._run_library_sync(context, FakeMonitor())

    assert cleans() == ['CleanLibrary("video", false)']


def test_additions_alone_do_not_trigger_a_clean(service, monkeypatch):
    """Cleaning walks the whole video library; it is not free."""
    context, _ = context_with_provider()
    _stub_sync(service, monkeypatch, movies=(3, 0), series=(2, 0))

    service._run_library_sync(context, FakeMonitor())

    assert cleans() == []
    assert any(command.startswith("UpdateLibrary") for command in kodistubs.builtins)
