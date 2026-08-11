# Provider findings

Everything in this project's design traces back to one diagnostic run. This document records what
was measured, so that later design decisions can be checked against evidence rather than memory.

**Date:** 2026-08-10
**Tool:** [`tools/iptv-diag.sh`](../tools/iptv-diag.sh)
**Panel:** Xtream Codes (host anonymised throughout as `panel.example.net:8080`; addresses replaced
with documentation ranges). The raw log stays out of the repository — regenerate it with the tool
above against your own account.

## Account

```
auth: 1                  status: Active           is_trial: 0
max_connections: 1       active_cons: 0
allowed_output_formats: ['m3u8', 'ts']
server: panel.example.net  port 80  https_port 443  protocol http  tz Europe/Amsterdam
```

## Results

| # | Request | Status | Payload | Reading |
| --- | --- | --- | --- | --- |
| 1 | TCP `panel.example.net:8080` | open | — | reachable |
| 2 | `player_api.php` (no action) | 200 | 490 B, 0.13 s | auth works |
| 3 | `get_live_categories` | 200 | 18.6 kB, 262 categories, 0.25 s | API healthy |
| 4 | `get_live_streams&category_id=1` | 200 | 15.4 kB, 47 channels, 0.25 s | per-category paging works |
| 5 | `get.php?type=m3u_plus` | **885** | 0 B | **endpoint disabled** |
| 5b | `…&output=ts` | **885** | 0 B | same |
| 5c | `…&output=m3u8` | **885** | 0 B | same |
| 6 | `get.php` without User-Agent | **885** | 0 B | not a UA problem — endpoint is simply off |
| 7 | `xmltv.php` | 200 | 31.06 MB, 42 178 `<programme>`, closes with `</tv>`, 0.81 s | EPG source healthy and complete |
| 8 | `get_short_epg&stream_id=843174` | 200 | `{"epg_listings":[]}` | empty for this channel — see open question |
| 9 | `live/…/843174.ts` | 200 | `video/mp2t`, 15.6 MB in 15 s, redirected to `203.0.113.44/live/play/<token>/843174` | works; redirects must be followed |
| 9b | `live/…/843174.m3u8` | 200 | `application/x-mpegURL`, 524 B | HLS variant available |
| 9c | `live/…/843174` (no extension) | **512** | 0 B | extension is mandatory |
| 10 | `.ts` without User-Agent | **454** | 0 B | **UA is mandatory for playback** |
| 11 | two concurrent `.ts` requests | 302 / 302 | 0 B each | inconclusive, see below |

Status codes 885, 512 and 454 are not HTTP standard codes. They are panel-specific rejection codes
emitted by the Xtream Codes reverse proxy. Treat them as "refused, with a reason encoded in the
number", not as transport errors.

## Conclusions that became requirements

1. **The channel list must come from `player_api.php`.** `get.php` returns nothing at all, for every
   variant, with and without a User-Agent. This is not a timeout, a rate limit, or a truncated
   download — the endpoint is switched off. Any design that depends on it cannot work here, which is
   exactly why IPTV Simple fails and TiviMate does not.
2. **Every request needs a User-Agent**, including the media request itself. An absent UA is refused
   with 454. This means the UA has to travel with the stream URL handed to Kodi, not just with the
   API calls.
3. **Stream URLs need an explicit extension.** `.ts` is preferred (direct MPEG-TS, no manifest
   round-trip); `.m3u8` is the fallback. No extension is refused with 512.
4. **Redirects must be followed.** Playback lands on a different host with a tokenised path. The
   token is tied to the request, so URLs cannot be cached across sessions.
5. **`max_connections` is 1.** One stream at a time. No background prefetch, no preview while
   browsing, and stop current playback before starting anything new.
6. **XMLTV is healthy.** 31 MB in under a second, complete and well-formed. There is no reason to
   proxy or cache it — IPTV Simple can fetch it directly.
7. **The media backend has brief bad windows — anywhere from under a second to tens of seconds —
   where it answers 502**, then recovers on its own. This is nginx failing to reach its upstream, not
   a connection-limit rejection (that is 456, see above). It is also not reliably tied to how many
   connections the client itself holds open: measured with a Python probe immediately followed by an
   independent `curl` request to the same URL, both attempts failed together as often as both
   succeeding, and — separately — a probe succeeding said nothing about whether Kodi's own request a
   few hundred ms later would also succeed. A pre-flight reachability probe was tried at three
   different points (added 0.1.3, reverted 0.1.4 on a "competing connection" theory, re-added 0.1.5
   after that theory failed to hold up) and **removed for good in 0.1.6**: it cannot fix a backend
   whose bad windows are themselves sub-second to multi-second, because there is no delay short enough
   to feel instant and long enough to reliably outlast one.
   **Also does not respond to a dropped-connection reconnect** (0.1.6/0.1.7,
   `inputstream.ffmpegdirect`'s `reconnect`/`reconnect_streamed` options): the failure mode here is a
   clean HTTP error status on the *first* request, not a connection that was fine and then dropped.
   ffmpeg has a separate option for that, `reconnect_on_http_error`, but the installed version of
   `inputstream.ffmpegdirect` does not forward it — confirmed by reading its source
   (`src/stream/FFmpegStream.cpp`, `GetFFMpegOptionsFromInput`): it only passes a fixed whitelist of
   protocol options to ffmpeg (`reconnect`, `reconnect_at_eof`, `reconnect_streamed`,
   `reconnect_delay_max`, a few others) and silently drops anything else. No Kodi item property or URL
   option can reach an option that specific add-on build does not forward.
   **The fix that actually works (0.1.8): a local HTTP proxy, `core/proxy.py`.** Kodi talks to
   `127.0.0.1` instead of the provider directly; the proxy makes the real request through
   `HttpClient.open_stream()` — the same retry/backoff every other provider call already gets, with
   full control over which statuses count as retryable — and only starts writing a response once it
   actually has one. This is the same class of resilience a player like TiviMate has via its own full
   HTTP stack (ExoPlayer's `LoadErrorHandlingPolicy`, ExoPlayer for TiviMate; libVLC's HTTP access
   module for VLC), neither of which is constrained by what a specific Kodi companion add-on happens to
   expose. See `docs/DESIGN.md`.
   A stuck/zombie session on the provider's own backend is a separate, real failure mode
   (`active_cons` can read 1 with every local client fully closed) but is not what this conclusion is
   about; it clears on its own after a few minutes and is not something the add-on can hurry along.

## Open question

Test 8 returned an empty `epg_listings` for stream 843174, while `xmltv.php` clearly contains 42 178
programmes. Possible causes: this particular channel has no EPG mapping, the panel only serves short
EPG for channels with a non-empty `epg_channel_id`, or the endpoint expects a different parameter
shape. To resolve: sample ten channels that have a non-empty `epg_channel_id` in
`get_live_streams` and compare. Until resolved, the add-on treats short-EPG as best-effort
enrichment and never depends on it — the EPG grid comes from XMLTV regardless.

## Re-running

```bash
tools/iptv-diag.sh
```

It prompts for the password, never echoes it, and writes a log with the password and username
masked. Point `BASE`/`USER`/`STREAM_ID` at a different account to profile another provider.
