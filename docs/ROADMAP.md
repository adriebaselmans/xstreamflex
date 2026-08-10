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
- [ ] Verified in a running Kodi 21 with the EPG grid populated

## Phase 4 — VOD and Series

- [x] Category and item browsing, artwork, metadata
- [x] Seasons and episodes
- [x] Playback with inputstream selection and fallback

## Phase 5 — plain M3U sources

- [x] `#EXTINF` attributes, `#EXTVLCOPT`, `#KODIPROP`, `#EXTGRP`
- [x] Same export path as Xtream

## Phase 6 — polish

- [x] In-add-on diagnostics screen
- [ ] Favourites
- [ ] Search across live, movies, series
- [ ] Dutch translation completed
- [ ] Cache management UI (size, clear)

## Later

- [ ] Catch-up / archive playback (`tv_archive` is already carried in the model)
- [ ] Multiple simultaneous providers merged into one export
- [ ] Kodi add-on repository packaging
- [ ] Resolve the empty `get_short_epg` question in
      [PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md#open-question)
