# Design

Module-level design. Read [ARCHITECTURE.md](ARCHITECTURE.md) first for the layering rules, and
[PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md) for the measurements the constraints come from.

## Tree

```
plugin.video.xstreamflex/
  addon.xml                     xbmc.python 3.0.1, video + service extension points
  addon.py                      plugin:// entry point
  service.py                    background export scheduler
  resources/
    settings.xml
    language/resource.language.{en_gb,nl_nl}/strings.po
    lib/
      core/
        __init__.py
        models.py               dataclasses
        errors.py               typed failures
        http.py                 HttpClient
        cache.py                Cache
        config.py               ProviderConfig, ProviderStore
        diagnostics.py          run_diagnostics()
        providers/
          base.py               Provider interface
          xtream.py             XtreamProvider
          m3u.py                M3UProvider
        export/
          m3u_writer.py         write_m3u()
          exporter.py           export_channels(), the cross-process export lock
          iptvsimple.py         IPTV Simple detection + setup hints
      kodiui/
        __init__.py
        router.py
        listing.py
        play.py
        dialogs.py
tools/
  iptv-diag.sh                  standalone shell diagnostics
  export_cli.py                 run an export without Kodi
tests/
  fixtures/*.json
  test_*.py
```

## core/models.py

Frozen dataclasses, no behaviour beyond derived properties:

```python
Account(auth, status, expires_at, is_trial, max_connections, active_connections,
        allowed_output_formats, server_url, server_port, https_port, protocol, timezone)
Category(id, name, kind)                     # kind: live | vod | series
Channel(id, name, category_id, number, logo, epg_channel_id, tv_archive, archive_days)
Movie(id, name, category_id, icon, container_extension, added, rating, plot, year, duration)
Series(id, name, category_id, cover, plot, genre, rating, last_modified)
Episode(id, series_id, season, episode, title, container_extension, plot, duration, added)
Programme(start, stop, title, description, channel_id)
StreamRef(url, headers, mime_type, inputstream)
```

`StreamRef` is the only thing that crosses into `kodiui/play.py`. It carries the headers the
provider requires so the UI layer never has to know that a User-Agent is mandatory.

## core/http.py

```python
class HttpClient:
    def __init__(self, user_agent, *, referer=None, origin=None,
                 connect_timeout=10, read_timeout=30, retries=3, backoff=0.6,
                 serialize=True, secrets=(), logger=None)

    def get_json(self, url, *, params=None) -> Any
    def get_text(self, url, *, params=None) -> str
    def download(self, url, dest_path, *, params=None, chunk=65536) -> int
    def probe(self, url, *, params=None, max_bytes=65536) -> ProbeResult
```

Behaviour that matters:

- **User-Agent** is set on the session and is non-optional. A provider that refuses an absent UA with
  454 will refuse it on every path, so there is no code path that omits it.
- **Retries** use `urllib3.Retry` on connect, read, and status 429/500/502/503/504, with exponential
  backoff. Panel rejection codes (454, 512, 885 and anything else ≥ 400 that is not in the retry set)
  are *not* retried — they are deterministic refusals and retrying only burns the connection quota.
- **Redirects** are followed; `ProbeResult` records the final URL and the hop count.
- **Serialisation**: a module-level `threading.Lock` guards every request when `serialize=True`,
  honouring `max_connections: 1`.
- **Secret masking**: `secrets` holds the password (and anything else sensitive). Every log line and
  every exception message passes through a scrubber that replaces those substrings with `***`. This
  is why URLs are logged at all — they are safe to log once scrubbed.
- **Status mapping** turns panel codes into typed errors:

  | Status | Error |
  | --- | --- |
  | 401, 403 | `AuthError` |
  | 454 | `AuthError` — "provider refused the request; User-Agent missing or blocked" |
  | 512 | `ProviderError` — "stream URL rejected; extension required" |
  | 885 | `EndpointDisabledError` |
  | 429 | `ConnectionLimitError` after retries |
  | 5xx | `TransientError` after retries |

  The mapping table is data, not `if`-chains, so a new panel code is a one-line addition.

## core/cache.py

`Cache(db_path)` with `get(key, default=None)`, `set(key, value, ttl)`, `get_stale(key)`,
`invalidate(prefix)`, `purge_expired()`. Values are JSON. sqlite is opened per call with
`check_same_thread=False` and a short busy timeout, because `addon.py` and `service.py` are separate
processes writing the same file.

`get_stale` exists for the degradation path: when a provider call fails and a stale row exists, the
caller serves the stale row and logs it, rather than presenting an empty list.

## core/config.py

`ProviderConfig` holds `id, label, kind, base_url, username, password, user_agent, referer,
max_connections, preferred_format, verify_tls, m3u_url, epg_url`.

`ProviderStore` reads and writes these as JSON in the add-on profile directory. Multiple providers
are supported from the start because the storage format is a list; the UI initially exposes one
"active provider" selector plus add/edit/remove.

Kept out of Kodi's `settings.xml` deliberately: Kodi settings are a flat key/value space and do not
model a variable-length list of provider records well. `settings.xml` holds only global preferences
(export interval, log level, preferred format default).

`base_url` is normalised on save: scheme defaulted to `http`, trailing slash stripped, port kept.

## core/providers/base.py

```python
class Provider(Protocol):
    def account(self) -> Account
    def categories(self, kind: str) -> list[Category]
    def channels(self, category_id: str | None = None) -> list[Channel]
    def movies(self, category_id: str | None = None) -> list[Movie]
    def movie_info(self, movie_id: str) -> dict
    def series(self, category_id: str | None = None) -> list[Series]
    def series_info(self, series_id: str) -> tuple[Series, list[Episode]]
    def short_epg(self, channel: Channel, limit: int = 2) -> list[Programme]
    def live_stream(self, channel: Channel) -> StreamRef
    def movie_stream(self, movie: Movie) -> StreamRef
    def episode_stream(self, episode: Episode) -> StreamRef
```

`M3UProvider` implements the live subset and raises `NotSupportedError` for VOD and series, which the
UI checks with `hasattr`-free capability flags (`provider.capabilities`).

## core/providers/xtream.py

Endpoint map, all on `player_api.php` with `username`/`password`:

| Method | `action=` |
| --- | --- |
| `account` | *(none)* |
| `categories('live')` | `get_live_categories` |
| `channels(cat)` | `get_live_streams&category_id=` |
| `categories('vod')` | `get_vod_categories` |
| `movies(cat)` | `get_vod_streams&category_id=` |
| `movie_info(id)` | `get_vod_info&vod_id=` |
| `categories('series')` | `get_series_categories` |
| `series(cat)` | `get_series&category_id=` |
| `series_info(id)` | `get_series_info&series_id=` |
| `short_epg(ch)` | `get_short_epg&stream_id=&limit=` |

Calling `channels()` without a category iterates the category list rather than requesting everything
at once. Each category response is cached separately, so a refresh after adding one category does
not re-download the rest.

Stream URLs:

```
live     {base}/live/{user}/{pass}/{stream_id}.{ext}     ext ∈ allowed_output_formats, prefer ts
movie    {base}/movie/{user}/{pass}/{stream_id}.{container_extension}
episode  {base}/series/{user}/{pass}/{episode_id}.{container_extension}
```

The extension is mandatory (512 without it). `container_extension` comes from the API; when it is
missing the code falls back to `mp4`, and on failure retries `mkv`.

Response shape defence: Xtream panels are inconsistent. `get_*` endpoints sometimes return a JSON
object with an error key instead of a list, sometimes return numbers as strings, sometimes return
`false`. Every parser therefore coerces types explicitly and raises `ParseError` with a truncated
body sample rather than letting a `TypeError` escape.

## core/providers/m3u.py

Streaming line parser (never loads the whole playlist into memory as one string):

- `#EXTINF:<dur> key="value" …,<display name>` — reads `tvg-id`, `tvg-name`, `tvg-chno`, `tvg-logo`,
  `group-title`, `catchup*`.
- `#EXTVLCOPT:http-user-agent=` / `http-referrer=` — become per-channel headers.
- `#KODIPROP:key=value` — passed through to the exported M3U and to `StreamRef`.
- `#EXTGRP:` — group fallback when `group-title` is absent.

Channel ids are synthesised from `tvg-id`, falling back to a stable hash of the URL, so exports are
reproducible across runs.

## core/export/m3u_writer.py

```python
def write_m3u(path, channels, url_for, *, user_agent, referer="",
              extra_props=None, renumber=False) -> ExportResult
```

Writes atomically: a temporary file in the same directory, `flush` + `fsync`, then `os.replace`.
IPTV Simple may read the file at any moment, and a half-written playlist is worse than a stale one.

Per channel:

```
#EXTINF:-1 tvg-id="npo1.nl" tvg-name="..." tvg-chno="12" tvg-logo="..." group-title="...",Channel Name
#EXTVLCOPT:http-user-agent=<ua>
#KODIPROP:mimetype=video/mp2t
http://host:8080/live/user/pass/12345.ts
```

`#EXTVLCOPT:http-user-agent` is written for every channel, unconditionally — the reference provider
refuses UA-less media requests with 454, and IPTV Simple's global `defaultUserAgent` is easy for a
user to miss. `#KODIPROP:mimetype` is written only for `.ts`, where it measurably speeds up player
selection; HLS is left for Kodi to sniff.

`tvg-id` carries the provider's `epg_channel_id` and is left **empty** when there is none. Falling
back to the stream id would emit an identifier that appears in no XMLTV feed, and would also stop
IPTV Simple from matching that channel by name instead.

`tvg-chno` cannot simply mirror the API's `num`: that value is an index *within a category*, so
across 262 categories the same number returns hundreds of times. The writer keeps a provider number
while it is still unique and otherwise hands out the next free one, so IPTV Simple is never left to
break ties on its own.

`ExportResult` carries counts, the output path, and any channels skipped with the reason, so the UI
can report "4 812 channels in 262 groups, 3 skipped (no stream id)".

Note that the exported URL contains the account password in the path. That is inherent to Xtream and
matches what every other client writes; the file is created with mode `0600`.

## core/export/iptvsimple.py

Detects whether `pvr.iptvsimple` is installed and which instance settings file applies.

One subtlety decides whether this works at all. Kodi writes `default="true"` on any setting still at
its schema value, and on load it reads the element and then calls `Reset()` on it. Writing a value
without removing that attribute means Kodi silently discards it — and on a fresh IPTV Simple install
every setting we care about carries the attribute. `apply_plan` therefore strips `default` from every
element it touches. Without that, `m3uPathType` reverts to "remote URL", the exported playlist is
never read, and the add-on reports success anyway. Since Kodi
20, IPTV Simple stores per-instance settings in
`userdata/addon_data/pvr.iptvsimple/instance-settings-<n>.xml`, and the ids in the add-on's own
`settings.xml` are marked `hidden_obsolete` and kept only for migration.

Relevant ids, verified against the Omega branch:

| Id | Value to use |
| --- | --- |
| `m3uPathType` | `0` (local path) |
| `m3uPath` | the exported `channels.m3u` |
| `epgPathType` | `1` (remote URL) |
| `epgUrl` | the provider's `xmltv.php` URL |
| `defaultUserAgent` | the provider's UA |
| `m3uRefreshMode` | `1` (interval) |
| `m3uRefreshIntervalMins` | `60` |

The module first tries to write the instance XML directly. If the file is absent, ambiguous (several
instances), or unwritable, it falls back to a **setup screen that displays the exact values to
enter**. Writing another add-on's private settings file is a best-effort convenience, never a
dependency — a silent failure there would be far worse than a one-time manual step.

Refresh is delegated to IPTV Simple's own `m3uRefreshMode`. The alternative, toggling the add-on via
`Addons.SetAddonEnabled`, forces a full PVR reload that discards channel order and group
customisation, so it is not used.

## core/diagnostics.py

`run_diagnostics(config, client, progress=None, sample_channel_id="") -> DiagnosticsReport` — the shell script's checks, in Python,
against the configured provider:

1. DNS resolution and TCP connect to the panel host and port.
2. `player_api.php` auth; report status, expiry, `max_connections`, `active_cons`,
   `allowed_output_formats`.
3. Category and channel counts for live, VOD, and series.
4. `get.php?type=m3u_plus` with a short timeout — expected to fail on affected providers, and
   reported as *informational*, since the add-on does not need it.
5. `xmltv.php` reachability, size, and whether the document terminates with `</tv>`.
6. One live stream probed as `.ts`, as `.m3u8`, without an extension, and once without a
   User-Agent — the four-way comparison that identifies the provider's rules.

Every check yields `(name, status, detail)` with status in `ok | warn | fail | info`. The report
renders as text for the UI and writes a redacted copy next to the log for sharing.

## kodiui

`router.py` maps `plugin://plugin.video.xstreamflex/?action=…` to handlers, with `url_for()` as the
single place that builds those URLs.

Menu:

```
Live TV        → export status, rebuild now, IPTV Simple setup
Movies         → categories → movies → play
Series         → categories → series → seasons → episodes → play
Favourites
Search         (live / movies / series)
Providers      → add, edit, select active
Diagnostics
Settings
```

Live channels are intentionally *not* browsable as a plain list in the add-on. Live TV belongs in
Kodi's PVR section, where the EPG grid lives; duplicating it in the add-on would invite the user to
play channels through a path with no EPG and no channel numbering.

`play.py` resolves a `StreamRef` and applies headers by appending them to the URL in Kodi's
`|Header=value` form, which is the only mechanism that survives the hand-off to the player. It picks
`inputstream.adaptive` for HLS when installed, `inputstream.ffmpegdirect` for MPEG-TS when installed,
and otherwise leaves Kodi's default.

`StreamRef.alternatives` carries the `ts` → `m3u8` → `direct_source` chain, but **nothing consumes it
yet**. Automatic failover would mean probing a second URL, and on a `max_connections: 1` account that
probe competes with the stream the user is already trying to watch. Choosing between "retry
automatically" and "respect the connection limit" needs a real box to measure on, so for now the
chain is data the UI does not act on. Tracked in [ROADMAP.md](ROADMAP.md).

`listing.py` builds `ListItem`s and fills `InfoTagVideo` through the Kodi 20+ setters
(`xbmc.InfoTagVideo`), not the removed `setInfo()` dictionary API, and sets `mediatype` so Kodi's
resume and library behaviour work for VOD.

## settings.xml

| Setting | Default | Purpose |
| --- | --- | --- |
| `export_enabled` | `true` | run scheduled exports |
| `export_interval_hours` | `6` | how often `service.py` rebuilds |
| `export_on_startup` | `true` | rebuild at Kodi launch if stale |
| `preferred_format` | `ts` | `ts` or `m3u8` |
| `user_agent` | mediaplayer default | overridable per provider |
| `request_timeout` | `30` | seconds |
| `serialize_requests` | `true` | honour a 1-connection limit |
| `cache_ttl_multiplier` | `1.0` | debugging aid |
| `log_level` | `info` | `debug` adds scrubbed request logging |

## Testing strategy

- **Unit**, no network: parsers fed recorded fixtures, including the malformed shapes real panels
  emit (`false`, `{"user_info":{...}}` where a list was expected, numeric strings).
- **Writer golden tests**: `write_m3u` output compared byte-for-byte against a checked-in expectation.
- **Error mapping**: each panel status code maps to the intended typed error.
- **`tools/export_cli.py`**: against a real account, prompts for the password, prints the export
  summary and the first entries. This is the end-to-end check that does not need Kodi.
- **In Kodi**: symlink the add-on, run `kodi --debug`, follow `~/.kodi/temp/kodi.log`.

## Deliberate non-goals

- **No C++ PVR binary add-on.** IPTV Simple already renders the EPG grid; a binary add-on would mean
  per-platform, per-Kodi-version builds for no user-visible gain.
- **No re-hosting of XMLTV.** It works; proxying it would add a failure mode and 31 MB of disk churn.
- **No catch-up/archive in v1.** The data model carries `tv_archive` and `archive_days` so it can be
  added, but it is not wired up.
