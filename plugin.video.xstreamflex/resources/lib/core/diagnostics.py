"""Provider diagnostics.

The Python counterpart of ``tools/iptv-diag.sh``. Its job is to turn "IPTV does not
work" into a specific, quotable statement about what the provider accepts — which is
exactly how this project's core requirement was established in the first place.

No check ever raises. A diagnostic that aborts on the first failure is useless.
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urlparse

from .config import KIND_XTREAM, ProviderConfig
from .errors import ProviderError
from .http import HttpClient
from .models import LIVE, SERIES, VOD

OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


@dataclass
class DiagnosticsReport:
    checks: List[Check] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def add(self, name: str, status: str, detail: str = "") -> Check:
        check = Check(name=name, status=status, detail=detail)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def verdict(self) -> str:
        if self.failed:
            return "%d check(s) failed" % len(self.failed)
        if any(c.status == WARN for c in self.checks):
            return "working, with warnings"
        return "all checks passed"

    def as_text(self) -> str:
        symbols = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]", INFO: "[info]"}
        lines = ["XstreamFlex diagnostics", ""]
        for check in self.checks:
            lines.append("%s %s" % (symbols.get(check.status, "[    ]"), check.name))
            if check.detail:
                for detail_line in check.detail.splitlines():
                    lines.append("        %s" % detail_line)
        lines += ["", "Verdict: %s" % self.verdict]
        return "\n".join(lines)


def run_diagnostics(config: ProviderConfig, client: HttpClient,
                    progress: Optional[Callable[[int, str], None]] = None,
                    sample_channel_id: str = "") -> DiagnosticsReport:
    report = DiagnosticsReport()
    steps = 7
    step = [0]

    def announce(label: str) -> None:
        step[0] += 1
        if progress:
            progress(int(step[0] * 100 / steps), label)

    # 1 - reachability
    announce("Checking connection")
    parsed = urlparse(config.base_url or "")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        report.add("Server address", FAIL, "No server URL configured.")
        return report
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
        report.add("DNS", OK, "%s -> %s" % (host, ", ".join(addresses)))
    except socket.gaierror as exc:
        report.add("DNS", FAIL, "%s does not resolve (%s)" % (host, exc))
        return report
    try:
        started = time.time()
        with socket.create_connection((host, port), timeout=8):
            pass
        report.add("TCP %s:%d" % (host, port), OK, "connected in %.2fs" % (time.time() - started))
    except OSError as exc:
        report.add("TCP %s:%d" % (host, port), FAIL, str(exc))
        return report

    if config.kind != KIND_XTREAM:
        announce("Checking playlist")
        _check_playlist_source(report, config, client)
        return report

    # 2 - account
    announce("Checking account")
    from .providers.xtream import XtreamProvider
    from .cache import NullCache

    provider = XtreamProvider(config, client, NullCache())
    account = None
    try:
        account = provider.account()
    except ProviderError as exc:
        report.add("Account", FAIL, "%s\n%s" % (exc.message, exc.detail))
    else:
        status = OK if account.is_active else FAIL
        expiry = (
            time.strftime("%Y-%m-%d", time.localtime(account.expires_at))
            if account.expires_at else "unlimited"
        )
        report.add("Account", status, "status=%s expires=%s trial=%s" % (
            account.status or "unknown", expiry, "yes" if account.is_trial else "no",
        ))
        limit_status = WARN if account.max_connections <= 1 else OK
        report.add("Connection limit", limit_status, (
            "max_connections=%d active=%d\n"
            "One connection means a second stream is refused while the first plays."
            % (account.max_connections, account.active_connections)
        ) if limit_status == WARN else "max_connections=%d active=%d" % (
            account.max_connections, account.active_connections))
        report.add("Output formats", INFO, ", ".join(account.allowed_output_formats) or "unknown")

    # 3 - catalogue
    announce("Counting channels")
    for kind, label in ((LIVE, "Live"), (VOD, "Movies"), (SERIES, "Series")):
        try:
            categories = provider.categories(kind)
        except ProviderError as exc:
            report.add("%s categories" % label, WARN, exc.message)
            continue
        detail = "%d categories" % len(categories)
        if kind == LIVE and categories:
            try:
                first = provider.channels(categories[0].id)
                detail += ", %d channels in '%s'" % (len(first), categories[0].name)
                if not sample_channel_id and first:
                    sample_channel_id = first[0].id
            except ProviderError as exc:
                detail += " (channel list failed: %s)" % exc.message
        report.add("%s categories" % label, OK if categories else WARN, detail)

    # 4 - get.php, informational only
    announce("Checking get.php")
    probe = client.probe(
        "%s/get.php" % config.base_url,
        params={"username": config.username, "password": config.password,
                "type": "m3u_plus"},
        max_bytes=4096, timeout=(8, 20),
    )
    if probe.ok and probe.bytes_read:
        report.add("get.php playlist", INFO,
                   "available (%d bytes sampled). Not used — the API path is preferred."
                   % probe.bytes_read)
    else:
        report.add("get.php playlist", INFO, (
            "unavailable (%s). This is the endpoint IPTV Simple needs on its own, and "
            "why it fails here. XstreamFlex does not use it."
            % (probe.error or "HTTP %d" % probe.status)
        ))

    # 5 - XMLTV
    announce("Checking EPG")
    if config.xmltv_url:
        epg = client.probe(config.xmltv_url, max_bytes=262144, timeout=(10, 60))
        if epg.ok and epg.bytes_read:
            report.add("XMLTV EPG", OK, "reachable, %d bytes sampled in %.2fs"
                       % (epg.bytes_read, epg.elapsed))
        else:
            report.add("XMLTV EPG", WARN,
                       "not reachable (%s). The EPG grid will stay empty."
                       % (epg.error or "HTTP %d" % epg.status))
    else:
        report.add("XMLTV EPG", WARN, "No EPG URL configured.")

    # 6 + 7 - stream shapes
    announce("Testing stream formats")
    if not sample_channel_id:
        report.add("Stream test", WARN, "No channel available to test.")
        return report

    for extension, label in (("ts", ".ts"), ("m3u8", ".m3u8")):
        url = provider.live_url(sample_channel_id, extension)
        probe = client.probe(url, max_bytes=32768, timeout=(8, 20))
        if probe.ok and probe.bytes_read:
            detail = "HTTP %d %s, %d bytes" % (probe.status, probe.content_type, probe.bytes_read)
            if probe.redirects:
                detail += "\nredirected %d time(s) to %s" % (probe.redirects, probe.final_url)
            report.add("Stream %s" % label, OK, detail)
        else:
            report.add("Stream %s" % label, WARN,
                       probe.error or "HTTP %d, %d bytes" % (probe.status, probe.bytes_read))

    announce("Testing without User-Agent")
    bare = client.probe(
        provider.live_url(sample_channel_id, "ts"),
        # None removes the session header entirely. An empty string would still send
        # "User-Agent:", which is a different request and not what we are testing.
        headers={"User-Agent": None}, max_bytes=8192, timeout=(8, 15),
    )
    if bare.ok and bare.bytes_read:
        report.add("Stream without User-Agent", INFO,
                   "accepted; this provider does not require a User-Agent.")
    else:
        report.add("Stream without User-Agent", INFO, (
            "refused (%s). A User-Agent is mandatory — XstreamFlex writes one into "
            "every playlist entry." % (bare.error or "HTTP %d" % bare.status)
        ))

    return report


def _check_playlist_source(report: DiagnosticsReport, config: ProviderConfig,
                           client: HttpClient) -> None:
    if not config.m3u_url:
        report.add("Playlist", FAIL, "No playlist URL or path configured.")
        return
    probe = client.probe(config.m3u_url, max_bytes=65536, timeout=(10, 60))
    if probe.ok and probe.bytes_read:
        report.add("Playlist", OK, "reachable, %d bytes sampled" % probe.bytes_read)
    else:
        report.add("Playlist", FAIL, probe.error or "HTTP %d" % probe.status)
