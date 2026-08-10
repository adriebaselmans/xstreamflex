# Roadmap

## Phase 1 — foundation

- [x] Provider diagnosis, documented in [PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md)
- [x] `tools/iptv-diag.sh`
- [x] Add-on skeleton: `addon.xml`, `settings.xml`, translations
- [x] `core/http.py` with UA enforcement, retries, redirects, serialisation, secret scrubbing
- [x] `core/cache.py`, `core/config.py`, `core/models.py`, `core/errors.py`

## Phase 2 — Xtream client

- [x] Account, categories, channels, VOD, series, short EPG
- [x] Stream URL construction with mandatory extension and fallback chain
- [x] Defensive parsing of the shapes real panels return
- [x] Unit tests on fixtures

## Phase 3 — Live TV via IPTV Simple  *(the primary goal)*

- [x] `core/export/m3u_writer.py` with atomic writes and per-channel User-Agent
- [x] `core/export/iptvsimple.py` detection and setup values
- [x] `service.py` scheduled rebuild
- [x] `tools/export_cli.py` for verification without Kodi
- [x] Verified against a real provider (2026-08-10): auth, 262 live categories, an export of
      219 channels over 5 categories with unique `tvg-chno` and correct `tvg-id`, and a stream
      from that playlist fetched with the exported User-Agent returning
      `200 video/mp2t`, 13.5 MB in 10 s
- [ ] Verified in a running Kodi 21 with the EPG grid populated

## Phase 4 — VOD and Series

- [x] Category and item browsing, artwork, metadata
- [x] Seasons and episodes
- [x] Playback with inputstream selection
- [x] Verified against a real provider (2026-08-10) with `export_cli.py --catalogue`:
      212 movie categories, `get_vod_info` populated, `.mp4` served as `200 video/mp4`;
      73 series categories, seasons and episodes parsed, `.mkv` served as
      `200 video/x-matroska`
- [ ] Strip the redundant "<show> - S01E01 - " prefix some panels put in episode titles

## Phase 5 — plain M3U sources

- [x] `#EXTINF` attributes, `#EXTVLCOPT`, `#KODIPROP`, `#EXTGRP`
- [x] Same export path as Xtream

## Phase 6 — polish

- [x] In-add-on diagnostics screen
- [ ] Favourites
- [ ] Search across live, movies, series
- [ ] Dutch translation completed
- [ ] Cache management UI (size, clear)

## Known gaps

- [ ] **Stream fallback is not wired up.** `StreamRef.alternatives` carries the
      `ts` → `m3u8` → `direct_source` chain, but nothing consumes it. Automatic failover means
      probing a second URL, which on a `max_connections: 1` account competes with the stream the
      user is trying to watch. Needs measuring on a real box before choosing a behaviour.
- [ ] **`get_short_epg` returned empty** for the one channel that was sampled; see
      [PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md#open-question). Nothing depends on it today.
- [ ] **Windows builds of Kodi get no cross-process export lock** — `export_lock` falls back to a
      no-op where `fcntl` is unavailable.

## Later

- [ ] Catch-up / archive playback (`tv_archive` is already carried in the model)
- [ ] Multiple simultaneous providers merged into one export
- [ ] Kodi add-on repository packaging
