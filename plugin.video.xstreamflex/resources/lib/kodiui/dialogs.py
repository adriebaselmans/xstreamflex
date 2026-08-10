"""Provider editing and report presentation."""
from __future__ import annotations

from typing import Optional

import xbmcgui

from core.config import KIND_M3U, KIND_XTREAM, ProviderConfig, normalise_base_url
from core.http import DEFAULT_USER_AGENT

KIND_LABELS = [("Xtream Codes API", KIND_XTREAM), ("M3U playlist", KIND_M3U)]


def edit_provider(existing: Optional[ProviderConfig] = None) -> Optional[ProviderConfig]:
    """Prompt for provider details. Returns None when the user cancels at any point."""
    dialog = xbmcgui.Dialog()
    config = existing or ProviderConfig()

    if existing is None:
        index = dialog.select("Source type", [label for label, _ in KIND_LABELS])
        if index < 0:
            return None
        config.kind = KIND_LABELS[index][1]

    label = dialog.input("Name for this provider", config.label)
    if not label:
        return None
    config.label = label

    if config.kind == KIND_XTREAM:
        base_url = dialog.input(
            "Server URL including port (http://host:8080)", config.base_url
        )
        if not base_url:
            return None
        config.base_url = normalise_base_url(base_url)

        username = dialog.input("Username", config.username)
        if not username:
            return None
        config.username = username

        password = dialog.input(
            "Password", config.password, type=xbmcgui.INPUT_ALPHANUM,
            option=xbmcgui.ALPHANUM_HIDE_INPUT,
        )
        if not password:
            return None
        config.password = password
    else:
        m3u_url = dialog.input("Playlist URL or local path", config.m3u_url)
        if not m3u_url:
            return None
        config.m3u_url = m3u_url
        epg_url = dialog.input("XMLTV EPG URL (optional)", config.epg_url)
        config.epg_url = epg_url or ""

    user_agent = dialog.input("User agent", config.user_agent or DEFAULT_USER_AGENT)
    config.user_agent = user_agent or DEFAULT_USER_AGENT

    # Re-run the constructor's normalisation now that fields have changed.
    config.__post_init__()
    return config


def show_report(title: str, text: str) -> None:
    xbmcgui.Dialog().textviewer(title, text)


def confirm(title: str, message: str) -> bool:
    return bool(xbmcgui.Dialog().yesno(title, message))


def notify(message: str, error: bool = False, milliseconds: int = 4000) -> None:
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    xbmcgui.Dialog().notification("XstreamFlex", message, icon, milliseconds)
