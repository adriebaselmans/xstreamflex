# Architecture

## The one rule

**`resources/lib/core/` must never import `xbmc`, `xbmcgui`, `xbmcplugin`, `xbmcaddon` or
`xbmcvfs`.**

Everything that talks to a provider, parses a response, or writes an M3U lives in `core/` and is
plain Python 3. Everything that draws a list, reads a setting, or starts a player lives in
`kodiui/`. The two meet at a small set of function calls.

This is not architectural purity for its own sake. It buys three concrete things:

- The provider logic runs under `pytest` on a normal machine, in milliseconds, with no Kodi
  installed. Most of the risk in this project is in HTTP behaviour and response parsing, and that is
  precisely the part that is hardest to debug inside Kodi.
- `tools/export_cli.py` can produce a real `channels.m3u` from a real account without Kodi, so the
  export can be verified end-to-end before it is ever loaded into a PVR client.
- A future PVR binary add-on, or a standalone CLI, could reuse `core/` untouched.

The dependency direction is strictly one way:

```
kodiui/  ──imports──▶  core/
core/    ──imports──▶  (stdlib, requests)
```

A CI check enforces this; see [DEVELOPMENT.md](DEVELOPMENT.md).

## Layers

```
┌──────────────────────────────────────────────────────────────────┐
│ kodiui/                                                          │
│   router.py     plugin:// URL dispatch                           │
│   context.py    handle, paths, settings, provider wiring         │
│   listing.py    ListItem + InfoTagVideo construction             │
│   play.py       stream resolution, inputstream selection         │
│   dialogs.py    progress, notifications, diagnostics report      │
└───────────────────────────┬──────────────────────────────────────┘
                            │  plain dataclasses, no Kodi types
┌───────────────────────────▼──────────────────────────────────────┐
│ core/                                                            │
│   models.py     Category, Channel, Movie, Series, Episode, …     │
│   errors.py     typed provider failures                          │
│   http.py       session, UA, retries, redirects, serialisation   │
│   cache.py      sqlite key/value with per-kind TTL               │
│   providers/    xtream.py, m3u.py  (common Provider interface)   │
│   export/       m3u_writer.py, exporter.py, iptvsimple.py        │
│   diagnostics.py                                                 │
└──────────────────────────────────────────────────────────────────┘
```

## Process model

Two entry points share the same `core/` code:

- **`addon.py`** — runs per user interaction. Kodi starts a fresh Python interpreter for each
  `plugin://` call, so nothing may be kept in memory between calls. All state lives in the sqlite
  cache or in settings.
- **`service.py`** — a long-lived `xbmc.Monitor` loop started at Kodi launch. It rebuilds the export
  on startup (if stale) and then on an interval. It is the only component that runs unattended, so
  it is deliberately small: check staleness, call the exporter, log, sleep.

A user-triggered rebuild and a scheduled one can otherwise overlap, so both take the same advisory
file lock (`export.lock`, via `fcntl.flock`) around an export. A thread lock would not do: these are
separate interpreters, and two overlapping runs would put two full category sweeps against an
account that permits one connection. The second run is refused rather than queued.

## Concurrency and the connection limit

Provider accounts commonly permit a single concurrent connection, and the reference account does
(see [PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md)). Two mechanisms enforce this:

1. `HttpClient` holds a lock around every request when `serialize=True`, so the add-on never has two
   API calls in flight *within* one process, and `export_lock` covers the cross-process case.
2. The UI never prefetches. Categories load when opened, EPG enrichment loads for the visible page
   only, and playback stops any current player before starting a new one.

The API and the media stream count against the same limit on some panels, which is why export runs
are skipped while something is playing rather than merely deprioritised.

## Failure model

`core/errors.py` defines the vocabulary:

| Error | Meaning | UI behaviour |
| --- | --- | --- |
| `AuthError` | credentials rejected, or account expired/banned | modal, points at Diagnostics |
| `EndpointDisabledError` | panel refused an endpoint outright (e.g. 885 on `get.php`) | modal explaining the alternative path |
| `ConnectionLimitError` | panel refused because another stream is active | notification, offer to stop playback |
| `TransientError` | timeout, 5xx, connection reset | retried automatically; surfaces only after retries |
| `ParseError` | 200 response whose body is not what the API contract promises | logged with a truncated body sample |

Anything unmapped becomes `ProviderError`. The UI never shows a raw traceback; it shows the message
and writes the detail to `kodi.log`.

## Caching

One sqlite file, `cache.db`, in the add-on's profile directory. Key is
`provider_id + ':' + logical_key`, value is JSON, with an expiry timestamp per row.

| Data | TTL | Reason |
| --- | --- | --- |
| account / `server_info` | 1 h | connection limits and expiry change rarely |
| categories (live/vod/series) | 24 h | very stable |
| channel list per category | 6 h | occasional channel churn |
| VOD / series metadata | 7 d | effectively immutable per id |
| short EPG | 15 min | "now/next" only |

Stream URLs are never cached — they redirect to tokenised, request-bound endpoints.

Cache misses degrade to a provider call; provider failures degrade to stale cache when one exists.
Serving stale data beats showing an empty list, so expiry is advisory on read failure.
