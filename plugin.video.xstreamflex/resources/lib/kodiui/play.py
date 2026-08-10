"""Turning a StreamRef into something Kodi will actually play."""
from __future__ import annotations

from typing import Optional

import xbmc
import xbmcgui
import xbmcplugin

from core.http import header_suffix
from core.models import StreamRef

INPUTSTREAM_ADAPTIVE = "inputstream.adaptive"
INPUTSTREAM_FFMPEGDIRECT = "inputstream.ffmpegdirect"


def _addon_available(addon_id: str) -> bool:
    try:
        import xbmcaddon
        xbmcaddon.Addon(addon_id)
        return True
    except Exception:
        return False


def stop_current_playback(wait_seconds: float = 3.0) -> None:
    """Free the connection slot before asking for another stream.

    Accounts with ``max_connections: 1`` refuse the second request outright, and a
    provider-side session takes a moment to clear after the player lets go.
    """
    player = xbmc.Player()
    if not player.isPlaying():
        return
    player.stop()
    monitor = xbmc.Monitor()
    waited = 0.0
    while player.isPlaying() and waited < wait_seconds:
        if monitor.waitForAbort(0.2):
            return
        waited += 0.2


def build_list_item(ref: StreamRef, label: str = "") -> xbmcgui.ListItem:
    # Headers only survive the hand-off to the player when appended to the URL in
    # Kodi's |Key=value form. A provider that answers 454 without a User-Agent will
    # fail every other way of passing them.
    url = ref.url + header_suffix(ref.headers)
    item = xbmcgui.ListItem(label=label or "", path=url)

    is_hls = ".m3u8" in ref.url.split("?", 1)[0]
    inputstream = ref.inputstream

    if not inputstream:
        if is_hls and _addon_available(INPUTSTREAM_ADAPTIVE):
            inputstream = INPUTSTREAM_ADAPTIVE
        elif not is_hls and _addon_available(INPUTSTREAM_FFMPEGDIRECT):
            inputstream = INPUTSTREAM_FFMPEGDIRECT

    if inputstream == INPUTSTREAM_ADAPTIVE:
        item.setProperty("inputstream", INPUTSTREAM_ADAPTIVE)
        item.setProperty("inputstream.adaptive.manifest_type", "hls")
        if ref.headers:
            item.setProperty(
                "inputstream.adaptive.stream_headers", header_suffix(ref.headers)[1:]
            )
    elif inputstream == INPUTSTREAM_FFMPEGDIRECT:
        item.setProperty("inputstream", INPUTSTREAM_FFMPEGDIRECT)
        item.setProperty("inputstream.ffmpegdirect.is_realtime_stream", "true")
        item.setProperty("inputstream.ffmpegdirect.stream_mode", "timeshift")
        item.setProperty("inputstream.ffmpegdirect.manifest_type", "hls" if is_hls else "")

    if ref.mime_type:
        item.setMimeType(ref.mime_type)
        item.setContentLookup(False)
    return item


def resolve(handle: int, ref: StreamRef, label: str = "",
            logger: Optional[callable] = None) -> None:
    if logger:
        logger("info", "resolving stream: %s" % ref.url.split("?", 1)[0])
    item = build_list_item(ref, label)
    xbmcplugin.setResolvedUrl(handle, True, item)


def fail(handle: int, message: str) -> None:
    xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
    xbmcgui.Dialog().notification("XstreamFlex", message, xbmcgui.NOTIFICATION_ERROR, 5000)
