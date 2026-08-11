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
#EXTVLCOPT:http-user-agent="<ua>"
#KODIPROP:mimetype=video/mp2t
http://host:8080/live/user/pass/12345.ts
```

`#EXTVLCOPT:http-user-agent` is written for every channel, unconditionally — the reference provider
refuses UA-less media requests with 454, and IPTV Simple's global `defaultUserAgent` is easy for a
user to miss. `#KODIPROP:mimetype` is written only for `.ts`, where it measurably speeds up player
selection; HLS is left for Kodi to sniff.

The value is **always double-quoted**, even though the M3U/VLC convention does not require it.
IPTV Simple's own parser (`PlaylistLoader::ReadMarkerValue`) reads an `EXTVLCOPT` value up to the
first unquoted space; a real User-Agent string such as `Mozilla/5.0 (Linux; Android 12) Chrome/120.0`
would otherwise be silently truncated to `Mozilla/5.0` downstream, and the provider then refuses the
incomplete header — the export looks correct in the file, but playback still fails.

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
`inputstream.adaptive` for HLS when installed, and `inputstream.ffmpegdirect` for everything else
(live MPEG-TS) when installed, falling back to Kodi's own native player otherwise. `StreamRef.live`
gates the realtime/timeshift properties — a VOD movie or episode is a static file even when its
container happens to be `.ts`, and timeshift mode expects a continuously growing live buffer, not a
file that can be seeked and has a known duration.

### VOD reliability: `core/proxy.py`, not an inputstream property

Movies and episodes (`ref.live is False`) are routed through a local HTTP proxy instead: `router._play()`
calls `play.proxied_ref()`, which asks `core.proxy.client_register()` to hand the URL to the proxy
server running inside `service.py`, and swaps in `http://127.0.0.1:19191/stream/<token>/<filename>` if
that succeeds. Falls back to the direct provider URL, unchanged, if the proxy isn't reachable (service
not started yet, or failed to bind its port).

This exists because the reference provider answers a **hard HTTP error on the very first request**
for a stream that will work moments later (see `docs/PROVIDER-FINDINGS.md` conclusion 7), and nothing
reachable from a Kodi add-on can retry that reliably:

- Kodi's own native player (`CCurlFile`) retries a failed open exactly once, ~35ms later — not
  enough to wait out even a brief blip.
- `inputstream.ffmpegdirect`'s ffmpeg-backed HTTP protocol (0.1.6/0.1.7) *can* retry, but only for a
  connection that drops after being established (`reconnect`/`reconnect_streamed`/`reconnect_at_eof`).
  The option for retrying a clean HTTP error status on the *first* request,
  `reconnect_on_http_error`, is not in the fixed whitelist of protocol options the installed version
  of that add-on forwards to ffmpeg (`src/stream/FFmpegStream.cpp`, `GetFFMpegOptionsFromInput`) — so
  it can never be reached from Python, no matter what URL options or item properties are set. This
  also required forcing `inputstream.ffmpegdirect.open_mode=ffmpeg` (left at its default, it silently
  picks `OpenMode::CURL` for a plain non-HLS/DASH/RTSP URL and hands I/O back to Kodi's own `CCurlFile`
  anyway — the addon's own debug log, `OpenWithCURL - IO handled by Kodi's cURL`, is what exposed this).
- A pre-flight reachability probe (0.1.3, reverted 0.1.4, re-added 0.1.5, removed 0.1.6) added latency
  without a reliability guarantee: success moments before Kodi's own request says nothing about that
  later, separate request's outcome, since the provider's bad windows are themselves sub-second to
  multi-second.

The proxy sidesteps all of it: Kodi talks to `127.0.0.1`, which is always instant and reliable, while
`core/proxy.py` makes the real request through `HttpClient.open_stream()` — full control over which
statuses to retry and how, the same retry/backoff every other provider call already gets — and only
starts writing a response once it actually has one. A `Range` header from a seek is translated into an
upstream `Range` request the same way, so seeking gets the same resilience as the initial open.
`core.proxy.DEFAULT_PORT` (19191) is fixed rather than discovered, because each `plugin://` action Kodi
invokes is a fresh, short-lived interpreter — it has no way to learn a port chosen by the long-lived
`service.py` process short of a fixed rendezvous point or a file on disk, and a fixed port is simpler.

`StreamRef.alternatives` carries the `ts` → `m3u8` → `direct_source` chain, but **nothing consumes it
yet**. That is a different question from the proxy above — it is about falling back to a *different*
URL, which costs a connection attempt against a URL that has not already been chosen, not about riding
out a failure on the one already in use. Tracked in [ROADMAP.md](ROADMAP.md).

`listing.py` builds `ListItem`s and fills `InfoTagVideo` through the Kodi 20+ setters
(`xbmc.InfoTagVideo`), not the removed `setInfo()` dictionary API, and sets `mediatype` so Kodi's
resume and library behaviour work for VOD.

## core/library_sync.py

```python
def sync_movies(root, movies, base_url) -> Tuple[int, int]      # (written, removed)
def sync_episodes(root, shows, base_url) -> Tuple[int, int]     # shows: (name, [Episode]) pairs
```

Getting an add-on's catalogue into Kodi's native Movies/TV Shows sections is normally a matter of
Settings > Media > Videos > Add videos, pointed at a `plugin://` URL, with a content type set. That
*is* documented and does work for many add-ons — it did not work reliably here: `router.sync_library`
(the `?action=sync_library&country=NL` route, exposed as a menu item) sets content type via the
in-Kodi flow correctly (confirmed in `MyVideos*.db`'s `path` table), but the actual library scan
either never triggers or finishes in 5-13ms having walked nothing — confirmed by the complete absence
of `[plugin.video.xstreamflex]` log lines during or after "Set content", meaning the add-on was never
even invoked. No setting found so far changes this.

`.strm` files sidestep plugin-source scanning entirely. A folder of them is, to Kodi's library
scanner, indistinguishable from a folder of real video files some other tool put there — the
best-trodden path in the whole Kodi ecosystem (every "add to library" video add-on uses it). Each file
is one line: the `plugin://` URL to open. `sync_library` writes one per movie, nested under a source
folder (`<source>/<title> (<id>).strm`), and one per episode, nested under a show folder with **no**
source folder above it (`<show>/S01E02 - <title> (<id>).strm`) — season/episode numbers in the
filename are what Kodi's scanner parses for TV shows, no NFO required. `<source>` is the provider's
own category name (e.g. `NL | Netflix`). `collect_movies`/`collect_shows_with_episodes` return
`(source, item)` pairs, one per category the item appears in, rather than bare lists, because the
provider's own categories are the only record of which source(s) carry a title.

`sync_movies` then collapses same-`id` pairs back into a single movie: one `.strm`/`.nfo` pair, filed
under whichever category was encountered first, with **every** matching category folded into the NFO
as its own `<tag>` (see `movie_nfo_content`). It is the movie's id, not the category name, that decides
uniqueness on disk — collapsing at the id level, not the (source, id) level, matters because on a real
account a title showing up under two *genuinely distinct* source apps (Netflix and Videoland) is the
rare case; showing up under a dozen *overlapping* categories from the same provider (`NL | Nieuw`,
`NL | Actie`, `NL | Films 2026`, ...) is the common one, and treating the latter as 12 different movies
produced exactly that many duplicate entries in Kodi's Movies view for what a user sees as one film.
Filtering/browsing "just this source" still works via Kodi's Tag view — it is just no longer also
encoded in which folder a movie sits in.

Movies and episodes deliberately use **different** depths for that same source information.
`movie_strm_path` puts it in the path (`<source>/<title>...`) because Kodi's *movie* scanner walks
arbitrarily deep looking for playable files and this was confirmed working against a real account.
`episode_strm_path` does not, and accepts a `source` argument only for API symmetry with
`collect_shows_with_episodes` — Kodi's *TV* scanner expects exactly one level of nesting
(`<TV root>/<Show>/<episode files>`) and, confirmed against the same account, does not descend past an
unrecognized intermediate folder to find the real show folders underneath it: with a source folder in
between, Kodi logged `No information found for item '...\<source folder>\'` for the source folder
itself, treating it as a failed show-match, and never looked inside it — leaving virtually the entire
series catalogue invisible in Kodi despite thousands of correctly-written `.strm` files sitting right
there on disk.

Losing the source folder for episodes lost the one thing it was for: being able to browse/filter
"just this show's source" (Netflix vs. Videoland) inside Kodi, which movies keep via their folder.
`movie_nfo_content`/`show_nfo_content` get it back a different way — a companion NFO next to each
`.strm` (`movie_nfo_path`: same basename, `.nfo` instead of `.strm`; `show_nfo_path`: the fixed
Kodi-recognized filename `tvshow.nfo`, once per show folder, describing the show rather than any one
episode) with the source as a `<tag>`. Kodi's library view can filter/browse by Tag the same way it
does Genre, for both movies and TV shows, regardless of folder structure — which is what makes this
work for series despite the folder-per-source approach being ruled out for them specifically.

The NFO is fully self-contained (title/plot/year/genre/rating/poster straight from the provider's own
`Movie`/`Series` data) rather than a stub that only carries the tag and otherwise expects Kodi to
scrape online — deliberately, because a real account showed many otherwise-fine titles (e.g. "Aquaman
and the Lost Kingdom", decorated with a resolution/country/id suffix purely for filename-uniqueness
reasons) failing to match TMDB and logging `No information found ... it won't be added to the
library`: an item could sync perfectly and still never appear in Kodi. A self-contained NFO removes
that failure mode entirely — no match to find, nothing to fail. Poster/cover URLs get the same
`|Header=value` suffix (`core.http.header_suffix`) as every other request to this provider, since Kodi
fetches NFO-referenced art itself, outside any hand-off this add-on otherwise controls.

`safe_filename`'s `max_length`, and `_budget_for`'s per-path length budget computed from the actual
`root` passed in, exist because a real provider was observed returning an episode `title` that is
already a fully-formatted `"<Show> - S01E02 - <Real Title>"` string — doubled against this module's
own prefix, the result exceeded Windows' 260-character `MAX_PATH`, which Windows reports as a bare
`OSError(2, "No such file or directory")` rather than anything that says "too long". A *fixed*
per-component cap could not fully guarantee staying under the limit regardless of how long the add-on's
own profile directory happens to be on a given machine (it was already 100+ characters on the machine
this was found on), so the budget is computed from `root`'s actual length instead, and the free-text
component (title) is shrunk down to whatever's left — down to an `_MIN_COMPONENT` floor past which
shrinking further would make the name meaningless anyway.

`episode_strm_path` deliberately does **not** repeat the show name inside the filename (just
`S01E02 - <title> (<id>).strm`, not `<show> - S01E02 - <title> (<id>).strm`) — Kodi's TV scanner
already identifies the show from the parent folder. Real shows on a real account still overflowed the
budget with the show name repeated: a moderately long show name (50-60 characters, nothing exotic)
counted three times over — once as the folder, once as the filename prefix, and again baked into the
title by the same provider quirk described above — was consistently enough on its own to exceed
`_MAX_PATH` regardless of how aggressively the title itself got truncated. Dropping the redundant
copy, rather than truncating harder, is what actually fixed the real failures (both now exactly at
the 240-character budget, having previously been 260+ and 266 characters).

That still cannot mathematically guarantee success if `root` alone plus the fixed parts (folder names,
the `S01E02` prefix, the `(id).strm` suffix) already exceed the budget — an inherent limit of available
path length, not something to paper over. The real backstop is that `sync_movies`/`sync_episodes` no
longer let one bad path take the rest of a catalogue of thousands of items down with it: each write is
wrapped in its own `try/except OSError`, calling an injectable `on_error(path, exc)` (both callers wire
it to `context.log("warning", ...)`) and moving on. Before this, a single unwritable path aborted the
whole sync silently — and since `service.py` only calls `write_sync_state` after a sync fully succeeds,
a crash left `is_sync_stale` permanently `True`, so the *same* doomed sync retried forever on every
tick, never getting further than whichever item it died on.

Comparing content before rewriting (`_write_if_changed`) matters: touching every file's mtime on every
sync would make Kodi's own *incremental* library scan see the whole catalogue as changed each run
instead of only what is actually new. `_prune` removes a `.strm` whose title (or source, or country
filter match) is no longer present, the same way deleting a downloaded file would — including, for
free, cleaning up the flat pre-source-folder layout from before this structure existed.

The user still has to add the resulting local folders (`context.library_dir/movies`,
`.../series` — shown by `?action=show_paths`) as ordinary Kodi video sources with content type set,
once. That is the well-trodden, reliable half of this; only the *plugin-source* half of Kodi's
library feature turned out not to be.

`service.py`'s `_run_library_sync` reruns this on the same schedule as the channel export
(`export_interval_hours`, gated by `library_sync_enabled`), then triggers Kodi's own scan for each of
the two folders that actually changed, via `xbmc.executebuiltin('UpdateLibrary("video", "<path>")')`
— the exact built-in action Kodi's own "Update library" menu item runs. An earlier version called the
lower-level `VideoLibrary.Scan` JSON-RPC method directly instead: it reported success (no error in the
response) but the resulting scan aborted after a single item, seconds after this process had just
finished writing tens of thousands of files — almost certainly a race with Kodi's own directory cache
that a manual trigger a few minutes later, on the same machine, did not hit. `UpdateLibrary` behaving
the same as the confirmed-working manual path was worth more than a theoretical reason to prefer the
JSON-RPC form. Without this step at all, new/removed `.strm` files would sit on disk unreflected until
the user (or Kodi's own periodic housekeeping, on whatever schedule that runs on) triggers a scan some
other way — Kodi's library does not watch the filesystem. Sync state is tracked in its own
`library-sync-state.json` (`core.library_sync.write_sync_state`/`is_sync_stale`), separate from the
channel export's `export-state.json`, so a stale-channels vs. stale-library check can never be
conflated.

Scanning a catalogue this size is genuinely slow when an online scraper (TheMovieDB) is attached —
each item is a real lookup, observed at roughly 40-90ms apiece in the log, so tens of thousands of
episodes can take a long time and will hit TMDB's rate limits. That is a property of the scraper the
user chose for the Kodi video source, not of this add-on; a "local information only" source (no
online matching) would scan near-instantly but without TMDB artwork/metadata. Worth knowing before
assuming a slow first scan means something is broken.

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
| `library_sync_enabled` | `true` | run scheduled `.strm` sync (reuses `export_interval_hours`) |
| `library_country` | `NL` | category-name prefix `sync_library`/scheduled sync includes |

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
