"""Everything Kodi-specific that the rest of the UI layer needs, in one place.

Holds the add-on handle, paths, settings, logger, and the wiring that turns a stored
provider configuration into a live provider object.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import xbmc
import xbmcaddon
import xbmcvfs

from core.cache import Cache
from core.config import KIND_M3U, KIND_XTREAM, ProviderConfig, ProviderStore
from core.http import DEFAULT_USER_AGENT, HttpClient
from core.providers.m3u import M3UProvider
from core.providers.xtream import XtreamProvider

_LEVELS = {
    "debug": xbmc.LOGDEBUG,
    "info": xbmc.LOGINFO,
    "warning": xbmc.LOGWARNING,
    "error": xbmc.LOGERROR,
}


class Context:
    def __init__(self, handle: int = -1, base_url: str = "") -> None:
        self.addon = xbmcaddon.Addon()
        self.addon_id = self.addon.getAddonInfo("id")
        self.addon_name = self.addon.getAddonInfo("name")
        self.addon_path = self.addon.getAddonInfo("path")
        self.handle = handle
        self.base_url = base_url or ("plugin://%s/" % self.addon_id)

        self.profile = xbmcvfs.translatePath(self.addon.getAddonInfo("profile"))
        os.makedirs(self.profile, exist_ok=True)
        self.export_dir = os.path.join(self.profile, "export")
        os.makedirs(self.export_dir, exist_ok=True)
        self.library_dir = os.path.join(self.profile, "library")
        os.makedirs(self.library_dir, exist_ok=True)

        self._log_level = self.setting("log_level") or "info"
        self._store: Optional[ProviderStore] = None
        self._cache: Optional[Cache] = None

    # -- basics ----------------------------------------------------------

    def log(self, level: str, message: str) -> None:
        if level == "debug" and self._log_level != "debug":
            return
        xbmc.log("[%s] %s" % (self.addon_id, message), _LEVELS.get(level, xbmc.LOGINFO))

    def reload(self) -> None:
        """Re-read state that another process may have changed.

        ``addon.py`` runs in its own interpreter, so a provider added through the UI
        is invisible to the long-lived service until it re-reads the store. Without
        this, a freshly added provider gets no scheduled export until Kodi restarts.
        """
        self._log_level = self.setting("log_level") or "info"
        if self._store is not None:
            self._store.load()

    def localise(self, string_id: int) -> str:
        return self.addon.getLocalizedString(string_id)

    def setting(self, key: str, default: str = "") -> str:
        try:
            value = self.addon.getSetting(key)
        except Exception:  # pragma: no cover - Kodi raises on unknown ids
            return default
        return value if value != "" else default

    def setting_bool(self, key: str, default: bool = False) -> bool:
        value = self.setting(key, "true" if default else "false")
        return str(value).lower() in ("true", "1", "yes")

    def setting_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.setting(key, str(default)))
        except (TypeError, ValueError):
            return default

    def setting_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.setting(key, str(default)))
        except (TypeError, ValueError):
            return default

    # -- paths -----------------------------------------------------------

    @property
    def providers_file(self) -> str:
        return os.path.join(self.profile, "providers.json")

    @property
    def cache_file(self) -> str:
        return os.path.join(self.profile, "cache.db")

    def playlist_path(self, provider: ProviderConfig) -> str:
        return os.path.join(self.export_dir, "channels-%s.m3u" % provider.id)

    @property
    def kodi_addon_data(self) -> str:
        """Kodi's ``userdata/addon_data`` — the parent of every add-on's profile."""
        return os.path.dirname(self.profile.rstrip(os.sep))

    # -- composed objects -------------------------------------------------

    @property
    def store(self) -> ProviderStore:
        if self._store is None:
            self._store = ProviderStore(self.providers_file)
        return self._store

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            self._cache = Cache(
                self.cache_file, ttl_multiplier=self.setting_float("cache_ttl_multiplier", 1.0)
            )
        return self._cache

    def http_client(self, config: ProviderConfig) -> HttpClient:
        return HttpClient(
            config.user_agent or DEFAULT_USER_AGENT,
            referer=config.referer,
            read_timeout=self.setting_int("request_timeout", 30),
            serialize=self.setting_bool("serialize_requests", True),
            verify_tls=config.verify_tls,
            secrets=config.secrets,
            logger=self.log,
        )

    def provider(self, config: Optional[ProviderConfig] = None):
        """Build the provider object for a configuration, or the active one."""
        config = config or self.store.active()
        if config is None:
            return None, None
        client = self.http_client(config)
        if config.kind == KIND_M3U:
            return M3UProvider(config, client, self.cache, self.log), config
        if config.kind == KIND_XTREAM:
            return XtreamProvider(config, client, self.cache, self.log), config
        return None, config


def build_context() -> Context:
    """Create a Context from the arguments Kodi passes to ``addon.py``."""
    handle = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else -1
    base_url = sys.argv[0] if sys.argv else ""
    return Context(handle=handle, base_url=base_url)
