"""Exercises core.proxy against a real local HTTP server.

No stubs here: a real upstream server (simulating the provider) and a real
core.proxy.ProxyServer bound to an ephemeral port, talked to with plain
``requests``, so what's tested is exactly the byte-for-byte forwarding and Range
translation Kodi's player will actually rely on.
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from core.proxy import DEFAULT_PORT, ProxyServer, client_register

PAYLOAD = b"0123456789" * 100  # 1000 bytes, easy to reason about Range math on


class _Upstream(BaseHTTPRequestHandler):
    #: Class-level so the handler (instantiated per request) can see state the
    #: test set up beforehand.
    fail_times = 0

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if _Upstream.fail_times > 0:
            _Upstream.fail_times -= 1
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        range_header = self.headers.get("Range")
        if range_header:
            start, _, end = range_header.replace("bytes=", "").partition("-")
            start = int(start)
            end = int(end) if end else len(PAYLOAD) - 1
            chunk = PAYLOAD[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "video/x-matroska")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)


@pytest.fixture
def upstream():
    _Upstream.fail_times = 0
    server = HTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d/movie.mkv" % server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy():
    server = ProxyServer()
    assert server.start(port=0)
    yield server
    server.stop()


def test_proxy_forwards_a_full_response(upstream, proxy):
    token = proxy.register(upstream, {"User-Agent": "UA"})
    url = proxy.url_for(token, upstream)

    response = requests.get(url, timeout=5)
    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["Content-Type"] == "video/x-matroska"


def test_proxy_translates_a_range_request(upstream, proxy):
    token = proxy.register(upstream, {})
    url = proxy.url_for(token, upstream)

    response = requests.get(url, headers={"Range": "bytes=10-19"}, timeout=5)
    assert response.status_code == 206
    assert response.content == PAYLOAD[10:20]
    assert response.headers["Content-Range"] == "bytes 10-19/1000"


def test_unknown_token_is_404(proxy):
    response = requests.get("http://127.0.0.1:%d/stream/nope/x.mkv" % proxy.port, timeout=5)
    assert response.status_code == 404


def test_transient_upstream_failure_is_retried_before_kodi_ever_sees_it(upstream, proxy):
    """The whole point: a 502 that would fail Kodi's own single, near-instant
    retry is absorbed here, because HttpClient's retry adapter runs before this
    module ever writes a response."""
    _Upstream.fail_times = 2
    token = proxy.register(upstream, {})
    url = proxy.url_for(token, upstream)

    response = requests.get(url, timeout=10)
    assert response.status_code == 200
    assert response.content == PAYLOAD


def test_client_register_returns_a_working_url(upstream, proxy):
    local_url = client_register(upstream, {"User-Agent": "UA"}, port=proxy.port)
    assert local_url is not None
    assert local_url.endswith("/movie.mkv")

    response = requests.get(local_url, timeout=5)
    assert response.status_code == 200
    assert response.content == PAYLOAD


def test_client_register_returns_none_when_nothing_is_listening():
    # Port 1 is a reserved low port nothing will ever be listening on locally.
    assert client_register("http://example.invalid/x.mkv", port=1, timeout=0.2) is None


def test_double_start_is_a_harmless_noop(proxy):
    assert proxy.start(port=proxy.port) is True


def test_start_fails_gracefully_on_an_unavailable_port(proxy):
    other = ProxyServer()
    assert other.start(port=proxy.port) is False
    other.stop()
