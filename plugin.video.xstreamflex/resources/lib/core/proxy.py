"""A local HTTP proxy between Kodi's player and a flaky provider.

Kodi's own player retries a failed connection exactly once, about 35ms later —
nowhere near enough to ride out this provider's brief backend hiccups (see
docs/PROVIDER-FINDINGS.md). Handing Kodi a URL on the provider directly means
every open, and every Range request a seek issues, gets exactly one shot.

This module makes Kodi talk to ``127.0.0.1`` instead, which is always instant and
reliable, while a request here does the real, patient work against the actual
provider through ``HttpClient`` (already retries 429/500/502/503/504 with
backoff) and only starts writing a response once it actually has one. A Range
header from a seek is translated into an upstream Range request, so seeking gets
the same resilience as the initial open.

Runs inside ``service.py``, the add-on's one long-lived process — each
``plugin://`` invocation is a fresh, short-lived interpreter that cannot host a
server spanning the length of a movie. A registration call over HTTP is what lets
that short-lived process hand the long-lived one a URL to serve.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple

from .http import HttpClient

#: Fixed so a fresh, short-lived ``plugin://`` invocation can find the
#: long-lived proxy without any discovery mechanism. Loopback-only, so the
#: choice is only at risk of colliding with another local service, not of
#: being reachable from outside this machine.
DEFAULT_PORT = 19191


class _Registry:
    """Maps an opaque token to the real URL and headers it stands in for.

    Tokens are handed out, never looked up by content, so nothing here needs to
    be findable by the provider URL — a fresh token per registration is enough
    and avoids any staleness question about reusing one.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[str, Dict[str, str]]] = {}

    def register(self, url: str, headers: Optional[Dict[str, str]]) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._entries[token] = (url, dict(headers or {}))
        return token

    def get(self, token: str) -> Optional[Tuple[str, Dict[str, str]]]:
        with self._lock:
            return self._entries.get(token)


class _Server(ThreadingHTTPServer):
    #: ``http.server`` defaults this on, and on Windows SO_REUSEADDR lets a
    #: second, unrelated process bind the same port silently instead of
    #: failing - the opposite of what a port-conflict guard needs.
    allow_reuse_address = False


def _make_handler(registry: _Registry, client: HttpClient, logger):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A002 - stdlib signature
            logger("debug", "proxy: " + (fmt % args))

        def do_POST(self):
            if self.path != "/register":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
                url = payload["url"]
                headers = payload.get("headers") or {}
            except Exception:
                self.send_error(400, "bad registration payload")
                return
            token = registry.register(url, headers)
            self._write_json({"token": token})

        def do_GET(self):
            self._serve(send_body=True)

        def do_HEAD(self):
            self._serve(send_body=False)

        def _write_json(self, payload) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve(self, send_body: bool) -> None:
            # /stream/<token>/<original-filename> - the filename is decoration
            # only, kept so Kodi still sees a familiar extension (.mkv etc.) on
            # the localhost URL instead of an opaque token, exactly as it would
            # have from the provider directly.
            parts = self.path.lstrip("/").split("/")
            if len(parts) < 2 or parts[0] != "stream":
                self.send_error(404)
                return
            entry = registry.get(parts[1])
            if entry is None:
                self.send_error(404, "unknown or expired stream token")
                return
            url, headers = entry
            range_header = self.headers.get("Range")
            try:
                upstream = client.open_stream(url, headers=headers, range_header=range_header)
            except Exception as exc:
                logger("warning", "proxy: upstream open failed: %s" % exc)
                self.send_error(502, "upstream did not answer")
                return
            try:
                self.send_response(upstream.status_code)
                content_type = upstream.headers.get("Content-Type")
                if content_type:
                    self.send_header("Content-Type", content_type)
                content_length = upstream.headers.get("Content-Length")
                if content_length is not None:
                    self.send_header("Content-Length", content_length)
                content_range = upstream.headers.get("Content-Range")
                if content_range:
                    self.send_header("Content-Range", content_range)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if send_body:
                    for chunk in upstream.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            break
            finally:
                upstream.close()

    return Handler


class ProxyServer:
    """Owns the background HTTP server thread for the lifetime of the service."""

    def __init__(self, logger=None) -> None:
        self._log = logger or (lambda level, message: None)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._registry = _Registry()
        # One shared client for every proxied request, regardless of which
        # provider it came from - the registration payload already carries
        # whatever headers that provider needs.
        self._client = HttpClient(logger=self._log)
        self.port: Optional[int] = None

    def start(self, port: int = DEFAULT_PORT) -> bool:
        """Bind and start serving in a daemon thread. Returns whether it started."""
        if self._httpd is not None:
            return True
        handler = _make_handler(self._registry, self._client, self._log)
        try:
            httpd = _Server(("127.0.0.1", port), handler)
        except OSError as exc:
            self._log("warning", "proxy: could not bind 127.0.0.1:%d (%s); "
                                  "playback falls back to direct URLs" % (port, exc))
            return False
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True,
                                         name="xstreamflex-proxy")
        self._thread.start()
        self._log("info", "proxy: listening on 127.0.0.1:%d" % port)
        return True

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._client.close()

    def register(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        return self._registry.register(url, headers)

    def url_for(self, token: str, original_url: str) -> str:
        filename = original_url.split("?", 1)[0].rsplit("/", 1)[-1] or "stream"
        return "http://127.0.0.1:%d/stream/%s/%s" % (self.port, token, filename)


def client_register(url: str, headers: Optional[Dict[str, str]] = None,
                     port: int = DEFAULT_PORT, timeout: float = 1.5) -> Optional[str]:
    """Ask an already-running :class:`ProxyServer` to proxy ``url``.

    Each ``plugin://`` action Kodi invokes runs in its own fresh, short-lived
    interpreter — it cannot host the server itself, only ask the one already
    running inside ``service.py`` to take on this URL. Returns the local URL to
    hand to Kodi, or ``None`` if the proxy is not reachable (not started yet,
    failed to bind its port, or the service simply is not running), so a caller
    can fall back to the direct provider URL rather than fail playback outright.
    """
    import requests

    try:
        response = requests.post(
            "http://127.0.0.1:%d/register" % port,
            json={"url": url, "headers": headers or {}},
            timeout=timeout,
        )
        response.raise_for_status()
        token = response.json()["token"]
    except Exception:
        return None
    filename = url.split("?", 1)[0].rsplit("/", 1)[-1] or "stream"
    return "http://127.0.0.1:%d/stream/%s/%s" % (port, token, filename)
