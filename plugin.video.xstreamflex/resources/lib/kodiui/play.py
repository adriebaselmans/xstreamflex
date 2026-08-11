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


def proxied_ref(ref: StreamRef) -> Optional[StreamRef]:
    """Swap a VOD ``StreamRef``'s URL for one served by the local proxy.

    Kodi's own player retries a failed connection exactly once, ~35ms later —
    not enough to ride out this provider's brief backend hiccups. ``core.proxy``
    (running inside ``service.py``, the add-on's one long-lived process) makes
    Kodi talk to 127.0.0.1 instead, which is always instant and reliable, while
    the actual retrying happens against the provider before Kodi ever sees a
    response. See docs/PROVIDER-FINDINGS.md.

    Returns ``None`` if the proxy is not reachable (service not running yet,
    failed to bind its port, ...), so the caller can fall back to handing Kodi
    the direct provider URL exactly as before, rather than fail playback outright.
    """
    from core.proxy import client_register
    local_url = client_register(ref.url, ref.headers)
    if local_url is None:
        return None
    return StreamRef(
        url=local_url, headers={}, mime_type=ref.mime_type,
        inputstream=ref.inputstream, alternatives=[], live=ref.live,
    )


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
    is_hls = ".m3u8" in ref.url.split("?", 1)[0]
    inputstream = ref.inputstream

    if not inputstream:
        if is_hls and _addon_available(INPUTSTREAM_ADAPTIVE):
            inputstream = INPUTSTREAM_ADAPTIVE
        elif not is_hls and _addon_available(INPUTSTREAM_FFMPEGDIRECT):
            # Kodi's own native player (CCurlFile) is not patient: measured on a
            # real installation, a failed open is retried exactly once, about
            # 35ms later — long enough to hit the same transient provider blip
            # again, not long enough to wait it out (see PROVIDER-FINDINGS.md).
            # ffmpegdirect's demuxer is ffmpeg's own libavformat HTTP protocol,
            # which understands real "reconnect" options (below) with actual
            # backoff, for a VOD file just as much as for a live one — routing
            # everything non-HLS through it, not just live TS, is what closes
            # the gap with a player like TiviMate that reconnects on its own.
            inputstream = INPUTSTREAM_FFMPEGDIRECT

    # Headers only survive the hand-off to the player when appended to the URL in
    # Kodi's |Key=value form. A provider that answers 454 without a User-Agent will
    # fail every other way of passing them.
    extra = {}
    if inputstream == INPUTSTREAM_FFMPEGDIRECT:
        extra = {"reconnect": "1", "reconnect_streamed": "1", "reconnect_delay_max": "15"}
        if ref.live:
            # A live feed should never legitimately hit EOF; a VOD file that has
            # actually finished playing should, so reconnecting there would hang
            # instead of ending playback normally.
            extra["reconnect_at_eof"] = "1"
    url = ref.url + header_suffix(dict(ref.headers or {}, **extra))
    item = xbmcgui.ListItem(label=label or "", path=url, offscreen=True)

    if inputstream == INPUTSTREAM_ADAPTIVE:
        item.setProperty("inputstream", INPUTSTREAM_ADAPTIVE)
        item.setProperty("inputstream.adaptive.manifest_type", "hls")
        if ref.headers:
            item.setProperty(
                "inputstream.adaptive.stream_headers", header_suffix(ref.headers)[1:]
            )
    elif inputstream == INPUTSTREAM_FFMPEGDIRECT:
        item.setProperty("inputstream", INPUTSTREAM_FFMPEGDIRECT)
        # Left at its default, ffmpegdirect picks its open mode itself, and for a
        # plain http(s) URL with an ordinary video mimetype (anything that is not
        # HLS/DASH/RTSP/etc.) that default is OpenMode::CURL — it hands I/O back to
        # Kodi's own CCurlFile, the exact single-retry code path this is meant to
        # get away from, silently ignoring the reconnect options above. Force
        # OpenMode::FFMPEG so ffmpeg's own HTTP protocol actually handles it.
        item.setProperty("inputstream.ffmpegdirect.open_mode", "ffmpeg")
        if ref.live:
            # Only a live MPEG-TS stream is a "realtime" feed. A VOD movie or
            # episode is just as likely to end in .ts (some panels store films
            # that way) but is a static file, not a broadcast — timeshift mode
            # expects a continuously growing live buffer and fails or refuses to
            # seek on anything else.
            item.setProperty("inputstream.ffmpegdirect.is_realtime_stream", "true")
            item.setProperty("inputstream.ffmpegdirect.stream_mode", "timeshift")
            item.setProperty("inputstream.ffmpegdirect.manifest_type", "hls" if is_hls else "")
        else:
            item.setProperty("inputstream.ffmpegdirect.is_realtime_stream", "false")

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
    xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
    xbmcgui.Dialog().notification("XstreamFlex", message, xbmcgui.NOTIFICATION_ERROR, 5000)
