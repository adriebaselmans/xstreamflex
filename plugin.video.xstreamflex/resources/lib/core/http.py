"""HTTP layer.

Everything the add-on sends to a provider goes through here, because the rules that
make IPTV panels work are all transport-level:

* a User-Agent is mandatory (the reference panel answers 454 without one),
* panel rejection codes are deterministic and must not be retried,
* redirects to tokenised hosts must be followed,
* an account with ``max_connections: 1`` cannot tolerate parallel requests,
* credentials appear in URLs, so nothing may be logged unscrubbed.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

try:  # urllib3 >= 2 renamed the parameter; Kodi ships either depending on build
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - defensive, urllib3 always present with requests
    Retry = None  # type: ignore[assignment]

from .errors import (
    AuthError,
    ConnectionLimitError,
    EndpointDisabledError,
    ParseError,
    ProviderError,
    TransientError,
)
from .models import ProbeResult

DEFAULT_USER_AGENT = "Mozilla/5.0 (Linux; Android 12; Build/QQ3A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"

#: Statuses worth retrying. Panel rejection codes are absent on purpose: they are
#: deterministic answers, and retrying them only consumes the connection quota.
RETRY_STATUSES = (429, 500, 502, 503, 504)

#: Panel-specific rejection codes observed in the wild, mapped to typed errors.
#: See docs/PROVIDER-FINDINGS.md. These are not HTTP standard codes.
STATUS_ERRORS: Dict[int, Tuple[type, str]] = {
    401: (AuthError, "Provider rejected the credentials (401)."),
    403: (AuthError, "Provider refused access (403). Account may be blocked."),
    404: (ProviderError, "Endpoint not found (404). Check the server URL and port."),
    454: (AuthError, "Provider refused the request (454). The User-Agent is missing or blocked."),
    456: (ConnectionLimitError, "Provider refused the request (456), usually a connection limit."),
    512: (ProviderError, "Provider rejected the stream URL (512). A file extension is required."),
    885: (EndpointDisabledError, "Provider has disabled this endpoint (885)."),
}


def _noop_logger(level: str, message: str) -> None:  # pragma: no cover - default sink
    pass


class Scrubber:
    """Replaces secrets with ``***`` in anything on its way to a log or an exception."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = [s for s in secrets if s and len(s) >= 3]

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


class HttpClient:
    """A configured session for one provider.

    Instances are cheap but hold a connection pool, so reuse one per provider for
    the lifetime of a request handler.
    """

    #: Guards every request when ``serialize`` is set. Class-level because Kodi may
    #: hold several client instances (provider + export) inside one interpreter, and
    #: the connection limit applies to the account, not to the object.
    _lock = threading.Lock()

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        *,
        referer: str = "",
        origin: str = "",
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 0.6,
        serialize: bool = True,
        verify_tls: bool = True,
        secrets: Iterable[str] = (),
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.timeout = (connect_timeout, read_timeout)
        self.serialize = serialize
        self.verify_tls = verify_tls
        self.scrub = Scrubber(secrets)
        self._log = logger or _noop_logger

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Connection": "keep-alive",
        })
        if referer:
            self.session.headers["Referer"] = referer
        if origin:
            self.session.headers["Origin"] = origin

        if Retry is not None and retries > 0:
            self.session.mount("http://", HTTPAdapter(max_retries=self._retry(retries, backoff)))
            self.session.mount("https://", HTTPAdapter(max_retries=self._retry(retries, backoff)))

    @staticmethod
    def _retry(total: int, backoff: float) -> "Retry":
        kwargs: Dict[str, Any] = dict(
            total=total,
            connect=total,
            read=total,
            status=total,
            backoff_factor=backoff,
            status_forcelist=RETRY_STATUSES,
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        # urllib3 2.x uses allowed_methods; 1.26 accepts it too but older builds
        # only know method_whitelist. Kodi bundles whichever the platform ships.
        try:
            return Retry(allowed_methods=frozenset(["GET", "HEAD"]), **kwargs)
        except TypeError:  # pragma: no cover - only on urllib3 < 1.26
            return Retry(method_whitelist=frozenset(["GET", "HEAD"]), **kwargs)

    # -- request helpers -------------------------------------------------

    def _request(self, method: str, url: str, *, params=None, stream: bool = False,
                 headers: Optional[Dict[str, str]] = None, timeout=None):
        safe_url = self.scrub(url)
        self._log("debug", "%s %s" % (method, safe_url))
        started = time.time()
        try:
            if self.serialize:
                with self._lock:
                    response = self.session.request(
                        method, url, params=params, stream=stream, headers=headers,
                        timeout=timeout or self.timeout, allow_redirects=True,
                        verify=self.verify_tls,
                    )
            else:
                response = self.session.request(
                    method, url, params=params, stream=stream, headers=headers,
                    timeout=timeout or self.timeout, allow_redirects=True,
                    verify=self.verify_tls,
                )
        except requests.Timeout as exc:
            raise TransientError(
                "Provider did not answer in time.", self.scrub(str(exc))
            ) from exc
        except requests.ConnectionError as exc:
            raise TransientError(
                "Could not reach the provider. Check the server address and your network.",
                self.scrub(str(exc)),
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError("Request failed.", self.scrub(str(exc))) from exc

        self._log("debug", "  -> %s in %.2fs" % (response.status_code, time.time() - started))
        return response

    def _check(self, response, url: str) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        mapped = STATUS_ERRORS.get(status)
        if mapped is not None:
            error_cls, message = mapped
            raise error_cls(message, self.scrub("%s -> %s" % (url, status)))
        if status in RETRY_STATUSES:
            # Retries are already exhausted by the adapter at this point.
            raise TransientError(
                "Provider is unavailable right now (%d)." % status,
                self.scrub(url),
            )
        raise ProviderError(
            "Provider returned an unexpected status (%d)." % status, self.scrub(url)
        )

    # -- public API ------------------------------------------------------

    def get_json(self, url: str, *, params=None) -> Any:
        response = self._request("GET", url, params=params)
        self._check(response, url)
        try:
            return response.json()
        except ValueError as exc:
            sample = self.scrub(response.text[:200].replace("\n", " "))
            raise ParseError(
                "Provider returned data that is not valid JSON.",
                "%s | body: %s" % (self.scrub(url), sample),
            ) from exc

    def get_text(self, url: str, *, params=None) -> str:
        response = self._request("GET", url, params=params)
        self._check(response, url)
        return response.text

    def iter_lines(self, url: str, *, params=None):
        """Stream a playlist line by line so a large M3U never lands in memory whole."""
        response = self._request("GET", url, params=params, stream=True)
        self._check(response, url)
        try:
            for raw in response.iter_lines(decode_unicode=False):
                if raw is None:
                    continue
                yield raw.decode("utf-8", "replace")
        finally:
            response.close()

    def open_stream(self, url: str, *, headers: Optional[Dict[str, str]] = None,
                     range_header: Optional[str] = None, timeout=None):
        """Open a streamed GET for a caller to forward byte-for-byte.

        Built for ``core.proxy``: Kodi's own player retries a failed connection
        exactly once, milliseconds later, which is not enough to ride out this
        provider's brief backend hiccups. The proxy lets Kodi talk to localhost
        instead, and uses this method to make the real, patient request — by the
        time this returns or raises, the session's retry adapter has already
        retried 429/500/502/503/504 with backoff, same as every other method here.

        Returns the raw ``requests.Response`` (``status_code``, ``headers``,
        ``iter_content``, ``close``) rather than a project type, since forwarding
        it as-is is the whole point — translating a Range request into an upstream
        Range request is what makes a seek get this same resilience.
        """
        req_headers = dict(headers or {})
        if range_header:
            req_headers["Range"] = range_header
        response = self._request("GET", url, stream=True, headers=req_headers, timeout=timeout)
        self._check(response, url)
        return response

    def download(self, url: str, dest_path: str, *, params=None, chunk: int = 65536) -> int:
        response = self._request("GET", url, params=params, stream=True)
        self._check(response, url)
        written = 0
        try:
            with open(dest_path, "wb") as handle:
                for block in response.iter_content(chunk_size=chunk):
                    if block:
                        handle.write(block)
                        written += len(block)
        finally:
            response.close()
        return written

    def probe(self, url: str, *, params=None, max_bytes: int = 65536,
              headers: Optional[Dict[str, str]] = None, timeout=None) -> ProbeResult:
        """Fetch just enough of a URL to learn whether it would play.

        Never raises: diagnostics want the failure recorded, not propagated. A HEAD
        is deliberately not used — panels routinely answer HEAD differently from the
        GET the player will actually make.
        """
        result = ProbeResult(url=self.scrub(url))
        started = time.time()
        response = None
        try:
            response = self._request("GET", url, params=params, stream=True,
                                     headers=headers, timeout=timeout)
            result.status = response.status_code
            result.content_type = response.headers.get("Content-Type", "")
            result.final_url = self.scrub(response.url)
            result.redirects = len(response.history)
            if 200 <= response.status_code < 300:
                for block in response.iter_content(chunk_size=16384):
                    result.bytes_read += len(block)
                    if result.bytes_read >= max_bytes:
                        break
        except ProviderError as exc:
            result.error = str(exc)
        except Exception as exc:  # pragma: no cover - probe must never propagate
            result.error = self.scrub(str(exc))
        finally:
            if response is not None:
                response.close()
            result.elapsed = time.time() - started
        return result

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def header_suffix(headers: Dict[str, str]) -> str:
    """Render headers in Kodi's ``|Key=value&Key2=value2`` URL form.

    This is the only way headers survive the hand-off from an add-on to Kodi's
    player, and it is also the form IPTV Simple understands in a playlist.
    """
    if not headers:
        return ""
    try:
        from urllib.parse import quote
    except ImportError:  # pragma: no cover - Python 2 is not supported
        raise
    parts = ["%s=%s" % (key, quote(value, safe="")) for key, value in sorted(headers.items())]
    return "|" + "&".join(parts)
