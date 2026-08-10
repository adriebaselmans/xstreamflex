import pytest
import requests

from core.errors import (
    AuthError,
    ConnectionLimitError,
    EndpointDisabledError,
    ParseError,
    ProviderError,
    TransientError,
)
from core.http import HttpClient, Scrubber, header_suffix
from core.config import ProviderConfig, normalise_base_url


class FakeResponse:
    def __init__(self, status=200, text="", json_data=None, headers=None):
        self.status_code = status
        self.text = text
        self._json = json_data
        self.headers = headers or {}
        self.url = "http://host/final"
        self.history = []

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json

    def close(self):
        pass


def client_with(monkeypatch, response):
    client = HttpClient("UA", secrets=["s3cret"])

    def fake_request(method, url, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(client.session, "request",
                        lambda method, url, **kw: fake_request(method, url, **kw))
    return client


@pytest.mark.parametrize("status,expected", [
    (401, AuthError),
    (403, AuthError),
    (454, AuthError),
    (456, ConnectionLimitError),
    (512, ProviderError),
    (885, EndpointDisabledError),
    (503, TransientError),
])
def test_panel_status_codes_map_to_typed_errors(monkeypatch, status, expected):
    client = client_with(monkeypatch, FakeResponse(status=status))
    with pytest.raises(expected):
        client.get_json("http://host/player_api.php")


def test_885_is_specifically_endpoint_disabled(monkeypatch):
    """The measured failure mode: get.php off, API fine."""
    client = client_with(monkeypatch, FakeResponse(status=885))
    with pytest.raises(EndpointDisabledError) as info:
        client.get_json("http://host/get.php")
    assert "885" in str(info.value)


def test_invalid_json_raises_parse_error_with_sample(monkeypatch):
    client = client_with(monkeypatch, FakeResponse(status=200, text="<html>nope</html>"))
    with pytest.raises(ParseError) as info:
        client.get_json("http://host/player_api.php")
    assert "nope" in info.value.detail


def test_timeout_becomes_transient(monkeypatch):
    client = client_with(monkeypatch, requests.Timeout("timed out"))
    with pytest.raises(TransientError):
        client.get_json("http://host/x")


def test_connection_error_becomes_transient(monkeypatch):
    client = client_with(monkeypatch, requests.ConnectionError("refused"))
    with pytest.raises(TransientError):
        client.get_json("http://host/x")


def test_secrets_are_scrubbed_from_errors(monkeypatch):
    client = client_with(monkeypatch, FakeResponse(status=403))
    with pytest.raises(AuthError) as info:
        client.get_json("http://host/player_api.php?password=s3cret")
    assert "s3cret" not in info.value.detail
    assert "***" in info.value.detail


def test_probe_never_raises(monkeypatch):
    client = client_with(monkeypatch, requests.ConnectionError("refused"))
    result = client.probe("http://host/live/u/p/1.ts")
    assert result.ok is False
    assert result.error


def test_user_agent_is_always_set():
    client = HttpClient("MyUA/1.0")
    assert client.session.headers["User-Agent"] == "MyUA/1.0"


def test_scrubber_ignores_short_secrets():
    scrub = Scrubber(["ab", "longenough"])
    assert scrub("ab longenough") == "ab ***"


def test_header_suffix_is_kodi_format():
    suffix = header_suffix({"User-Agent": "UA/1.0", "Referer": "http://r/"})
    assert suffix == "|Referer=http%3A%2F%2Fr%2F&User-Agent=UA%2F1.0"


def test_header_suffix_empty_for_no_headers():
    assert header_suffix({}) == ""


@pytest.mark.parametrize("raw,expected", [
    ("host:8080", "http://host:8080"),
    ("http://host:8080/", "http://host:8080"),
    ("http://host:8080/player_api.php?username=a", "http://host:8080"),
    ("http://host:8080/get.php?username=a&type=m3u_plus", "http://host:8080"),
    ("https://host/xmltv.php", "https://host"),
    ("  http://host:8080  ", "http://host:8080"),
    ("", ""),
])
def test_base_url_normalisation(raw, expected):
    assert normalise_base_url(raw) == expected


def test_xmltv_url_is_derived_for_xtream():
    config = ProviderConfig(base_url="http://host:8080", username="u", password="p")
    assert config.xmltv_url == "http://host:8080/xmltv.php?username=u&password=p"


def test_explicit_epg_url_wins():
    config = ProviderConfig(base_url="http://host:8080", username="u", password="p",
                            epg_url="http://other/epg.xml")
    assert config.xmltv_url == "http://other/epg.xml"


def test_incomplete_config_is_flagged():
    assert ProviderConfig(base_url="http://host").is_complete is False
    assert ProviderConfig(base_url="http://host", username="u",
                          password="p").is_complete is True


def test_invalid_preferred_format_falls_back_to_ts():
    assert ProviderConfig(preferred_format="webm").preferred_format == "ts"
