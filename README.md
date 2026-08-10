# XstreamFlex

A Kodi add-on for IPTV that talks to Xtream Codes panels the way modern IPTV players do — through
`player_api.php`, per category, with proper HTTP headers — instead of relying on one giant
`get.php` playlist download.

It exists because IPTV Simple, the default way to watch IPTV in Kodi, only knows how to fetch a full
M3U playlist from `get.php`. A growing number of providers have disabled or throttled that endpoint
while keeping their API fully functional. On those providers IPTV Simple fails completely, even
though apps like TiviMate work fine against the same account.

XstreamFlex fixes that without giving up Kodi's native Live TV experience:

- It reads your channel list from the **Xtream API**, category by category, cached on disk.
- It writes a clean, local **M3U** that IPTV Simple reads instead of contacting your provider.
- You keep Kodi's full **EPG grid**, channel groups, and PVR features.
- **VOD and Series** — which IPTV Simple cannot do at all — live in the add-on's own browser.
- A built-in **diagnostics** screen tells you exactly what your provider accepts and refuses.

Target: Kodi 21 "Omega" (`xbmc.python` 3.0.1). Sources: Xtream Codes API and plain M3U/M3U8.

## Why not just use IPTV Simple?

IPTV Simple is a fine PVR client. The problem is how it gets its data. Measured against a real
provider (see [docs/PROVIDER-FINDINGS.md](docs/PROVIDER-FINDINGS.md)):

| Request | Result |
| --- | --- |
| `get.php?type=m3u_plus` | HTTP 885, 0 bytes — endpoint disabled |
| `player_api.php` (auth) | HTTP 200, 262 categories, ~0.25 s |
| stream `.ts` with User-Agent | HTTP 200, `video/mp2t` |
| same stream, no User-Agent | HTTP 454 — refused |

IPTV Simple depends on the first row and has no option to use the second. That is the entire bug.
XstreamFlex bridges the two: it does the API work and hands IPTV Simple a local file.

## How it works

```
Xtream panel                 XstreamFlex add-on                    Kodi
─────────────                ──────────────────                    ────
player_api.php   ──────────▶ HTTP layer (UA, retries, redirects)
  get_live_categories        cache (sqlite, TTL per data type)
  get_live_streams           │
  get_vod_*                  ├──▶ M3U exporter ──▶ channels.m3u ──▶ IPTV Simple ──▶ TV / EPG grid
  get_series_*               │
  get_short_epg              └──▶ add-on browser ────────────────▶ VOD, Series, Favourites

xmltv.php ───────────────────────────────────────────────────────▶ IPTV Simple (direct, it works)
```

The add-on never asks the provider for a bulk playlist. IPTV Simple never talks to the provider at
all for channels — it reads a file on disk that the add-on refreshes on a schedule.

## Install

Not yet published to a repository. For now, install from the source tree:

```bash
git clone <this-repo> ~/src/xstreamflex
ln -s ~/src/xstreamflex/plugin.video.xstreamflex ~/.kodi/addons/plugin.video.xstreamflex
```

Restart Kodi, then enable the add-on under *Add-ons → My add-ons → Video add-ons*.

You also need **PVR IPTV Simple Client** installed and enabled for the Live TV part.

## Configure

1. Open XstreamFlex → **Providers → Add provider**.
2. Enter server URL (including port), username, password. Pick *Xtream Codes API*.
3. Run **Diagnostics**. It reports account status, connection limit, allowed output formats, and
   whether your provider accepts each stream URL shape. Fix anything it flags before continuing.
4. Run **Export → Rebuild channel list**. This writes `channels.m3u` into the add-on's data folder
   and prints the exact path.
5. In IPTV Simple's settings, set the M3U *Location* to **Local path** and point it at that file.
   Set the EPG to your provider's `xmltv.php` URL. XstreamFlex shows both values ready to copy.
6. Enable Kodi's PVR and restart. Channels and the EPG grid appear under **TV**.

Step 5 is one-time. After that the add-on rewrites the same file and IPTV Simple picks up changes on
its own refresh interval.

## Connection limits

Many accounts allow only one concurrent stream. XstreamFlex respects this: provider requests are
serialised, nothing is prefetched in the background, and playback of a new item stops the previous
one first. If your account allows more, raise the limit in the provider settings.

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md) — module-by-module design and data flow
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layering rules and why the core is Kodi-free
- [docs/PROVIDER-FINDINGS.md](docs/PROVIDER-FINDINGS.md) — the measurements this project is built on
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — running tests and the add-on without Kodi
- [docs/ROADMAP.md](docs/ROADMAP.md) — what is built and what is next

## Credentials and privacy

Credentials are stored in Kodi's add-on settings, in your profile directory — the same place IPTV
Simple keeps them. They are never sent anywhere except to your own provider. Log output masks
passwords, and the diagnostics report is written with credentials redacted so it is safe to share
when asking for help.

## Licence

GPL-2.0-or-later, matching Kodi's own licensing.
