"""Provider configuration and its on-disk store.

Kept out of Kodi's ``settings.xml`` because that is a flat key/value space and models
a variable-length list of provider records badly. Kodi settings hold global
preferences only.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .http import DEFAULT_USER_AGENT

KIND_XTREAM = "xtream"
KIND_M3U = "m3u"


def normalise_base_url(url: str) -> str:
    """Accept what users actually paste and return something joinable.

    Handles a missing scheme, a trailing slash, and a full ``player_api.php`` or
    ``get.php`` URL pasted straight from a provider e-mail.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    for suffix in ("/player_api.php", "/get.php", "/panel_api.php", "/xmltv.php"):
        marker = url.lower().find(suffix)
        if marker != -1:
            url = url[:marker]
            break
    return url.rstrip("/")


@dataclass
class ProviderConfig:
    label: str = "Provider"
    kind: str = KIND_XTREAM
    base_url: str = ""
    username: str = ""
    password: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    referer: str = ""
    max_connections: int = 1
    preferred_format: str = "ts"
    verify_tls: bool = True
    m3u_url: str = ""
    epg_url: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        self.base_url = normalise_base_url(self.base_url)
        if self.preferred_format not in ("ts", "m3u8"):
            self.preferred_format = "ts"
        self.max_connections = max(1, int(self.max_connections or 1))

    # -- derived URLs ----------------------------------------------------

    @property
    def api_url(self) -> str:
        return "%s/player_api.php" % self.base_url

    @property
    def xmltv_url(self) -> str:
        """The EPG URL to hand to IPTV Simple.

        An explicit ``epg_url`` wins; otherwise the standard Xtream endpoint. Plain
        M3U providers have no default, so they return whatever was configured.
        """
        if self.epg_url:
            return self.epg_url
        if self.kind == KIND_XTREAM and self.base_url and self.username:
            return "%s/xmltv.php?username=%s&password=%s" % (
                self.base_url, self.username, self.password,
            )
        return ""

    @property
    def secrets(self) -> List[str]:
        return [s for s in (self.password,) if s]

    @property
    def is_complete(self) -> bool:
        if self.kind == KIND_XTREAM:
            return bool(self.base_url and self.username and self.password)
        return bool(self.m3u_url)

    def describe(self) -> str:
        if self.kind == KIND_XTREAM:
            return "%s (%s)" % (self.label, self.base_url or "not configured")
        return "%s (M3U)" % self.label

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class ProviderStore:
    """A JSON list of providers plus the id of the active one."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._data: Dict[str, Any] = {"active": "", "providers": []}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("providers"), list):
                self._data = data
        except (OSError, ValueError):
            pass  # first run, or a corrupted file we deliberately overwrite on save

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)  # the file holds provider passwords
        except OSError:
            pass

    def all(self) -> List[ProviderConfig]:
        return [ProviderConfig.from_dict(p) for p in self._data.get("providers", [])]

    def get(self, provider_id: str) -> Optional[ProviderConfig]:
        for provider in self.all():
            if provider.id == provider_id:
                return provider
        return None

    def active(self) -> Optional[ProviderConfig]:
        active_id = self._data.get("active") or ""
        found = self.get(active_id) if active_id else None
        if found is not None:
            return found
        providers = self.all()
        return providers[0] if providers else None

    def set_active(self, provider_id: str) -> None:
        self._data["active"] = provider_id
        self.save()

    def upsert(self, config: ProviderConfig) -> None:
        providers = [p for p in self._data.get("providers", []) if p.get("id") != config.id]
        providers.append(config.to_dict())
        self._data["providers"] = providers
        if not self._data.get("active"):
            self._data["active"] = config.id
        self.save()

    def remove(self, provider_id: str) -> None:
        self._data["providers"] = [
            p for p in self._data.get("providers", []) if p.get("id") != provider_id
        ]
        if self._data.get("active") == provider_id:
            remaining = self._data["providers"]
            self._data["active"] = remaining[0]["id"] if remaining else ""
        self.save()
