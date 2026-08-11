# Installing XstreamFlex on a new machine

Step-by-step for setting this up from scratch — written for Ubuntu + Kodi, with notes for other
platforms where the steps differ. If you only want the short version, see the
[README's Install section](../README.md#install); this document goes further, through provider
setup, Live TV, and the movie/series library integration.

## 0. What you need first

- **Kodi 21 "Omega"** or newer, installed and started at least once.
- Your provider's **server URL (with port), username, and password** — the same ones you'd type into
  any Xtream-compatible app.
- **`PVR IPTV Simple Client`** enabled for Live TV. It ships with Kodi; you just need to turn it on
  (step 4 below covers this).

You do **not** need Python installed separately on the Kodi machine — Kodi bundles its own, and the
add-on's one dependency, `script.module.requests`, installs automatically from Kodi's own repository
when you install the ZIP.

### Installing Kodi on Ubuntu, if you haven't yet

```bash
sudo add-apt-repository ppa:team-xbmc/ppa
sudo apt update
sudo apt install kodi
```

This is the official PPA and is what these instructions assume. If you instead use the Snap
(`snap install kodi`) or Flatpak build, everything below still applies — only the
**userdata folder path** differs, noted where it matters:

| Install method | `userdata` path |
| --- | --- |
| APT / PPA (assumed below) | `~/.kodi/userdata/` |
| Snap | `~/snap/kodi/common/.kodi/userdata/` |
| Flatpak | `~/.var/app/tv.kodi.Kodi/data/userdata/` |

## 1. Build the install ZIP

Do this on any machine with Python 3 and `git` — it does not have to be the Kodi machine.

```bash
git clone https://github.com/adriebaselmans/xstreamflex.git
cd xstreamflex
python3 tools/package.py
```

This prints the path to the ZIP it built, e.g. `dist/plugin.video.xstreamflex-0.2.1.zip`. The script
refuses to produce a ZIP with a shape Kodi would reject (wrong top-level folder, bytecode included,
etc.), so if it succeeds, the ZIP will install cleanly.

Get that ZIP onto the Kodi machine however is convenient — `scp`, a USB stick, a shared folder. If
you cloned the repo directly on the Kodi machine, it's already there; skip ahead.

## 2. Install the add-on in Kodi

1. **Settings → System → Add-ons** → turn on **Unknown sources**, accept the warning. (One-time; Kodi
   blocks installing from a ZIP otherwise.)
2. **Settings → Add-ons → Install from zip file** → browse to the ZIP → confirm.
3. Kodi installs `script.module.requests` automatically, then shows a notification. XstreamFlex now
   appears under **Add-ons → Video add-ons**.

If install fails with an unhelpful error, rebuild the ZIP with `tools/package.py` rather than any
other zip tool — Kodi is strict about the internal path structure, and generic zip utilities (in
particular Windows' own Compress-Archive) can produce a ZIP that fails to install for reasons Kodi
does not report clearly.

## 3. Add your provider

1. Open **XstreamFlex** (Add-ons → Video add-ons → XstreamFlex).
2. **Providers → Add provider** → pick **Xtream Codes API** → enter server URL (with port), username,
   password.
3. Run **Diagnostics**. It reports your account's connection limit, allowed formats, and whether your
   provider accepts each stream URL shape — fix anything it flags before continuing. The report has
   credentials redacted, so it's safe to paste when asking for help.

## 4. Live TV (via IPTV Simple)

1. **Settings → Add-ons → My add-ons → PVR clients → PVR IPTV Simple Client** → enable it.
2. Back in XstreamFlex: **Export → Rebuild channel list now**. This writes a local M3U file and shows
   you its exact path.
3. Open IPTV Simple's own settings (from the PVR clients screen, or **Settings → PVR & Live TV →
   General → your IPTV Simple instance**):
   - M3U/Playlist **Location**: **Local path**, pointed at the file from step 2.
   - **EPG Location**: your provider's `xmltv.php` URL — XstreamFlex's **"Set up IPTV Simple"** menu
     shows the exact value to copy, and offers to fill in both automatically if it can find IPTV
     Simple's own settings file.
4. **Settings → PVR & Live TV → General** → enable **PVR** if it isn't already, restart Kodi.
   Channels and the EPG grid appear under **TV**.

This is one-time. XstreamFlex rewrites the same M3U file on a schedule (default every 6 hours,
**Settings → Add-ons → XstreamFlex → Export**); IPTV Simple picks up the changes on its own refresh
interval without you touching either setting again.

## 5. Movies & TV Shows in Kodi's own library

VOD and series browse inside the add-on itself out of the box (**XstreamFlex → Movies** /
**Series**). To get them into Kodi's native **Movies** and **TV Shows** home-screen sections instead
(with posters, the library grid, etc.), two steps:

1. In XstreamFlex's main menu, run **"Sync NL movies & series to Kodi library"** once. (`NL` is the
   default category-name prefix — **Settings → Add-ons → XstreamFlex → Library** changes it if your
   provider uses a different country code, or clears it to include everything.) This writes one small
   `.strm` file per movie/episode — not the video itself, just the address to play it from — into two
   local folders under the add-on's profile directory. **XstreamFlex → Show paths** prints the exact
   paths for your machine; on a typical Ubuntu/APT install they look like:
   ```
   ~/.kodi/userdata/addon_data/plugin.video.xstreamflex/library/movies
   ~/.kodi/userdata/addon_data/plugin.video.xstreamflex/library/series
   ```
2. Add each of those two folders as an ordinary Kodi video source, once:
   **Settings → Media → Videos → Add videos** → click directly into the empty path field (not
   "Browse") and type/paste the folder path → **OK** → name it → **OK** → when Kodi asks
   *"This directory contains"*, pick **Movies** (or **TV shows** for the series folder) → choose a
   scraper (The Movie Database is fine) → let it scan.

After that, XstreamFlex keeps the `.strm` files themselves up to date automatically (same schedule as
the channel export) and tells Kodi to rescan whenever something actually changed — nothing further to
do. See [docs/DESIGN.md](DESIGN.md#corelibrary_syncpy) for why `.strm` files are used instead of
pointing the video source directly at the add-on (the more obvious-looking approach, which turned out
unreliable — Kodi accepts the setting but silently never scans it).

## 6. Sanity-check the install

- **Live TV**: Kodi's **TV** section shows channels and an EPG grid.
- **A movie plays and can be fast-forwarded** without "One or more items failed to play." (If your
  provider is flaky the same way the one XstreamFlex was built against is, the add-on's local
  playback proxy — `core/proxy.py`, listening on `127.0.0.1:19191`, nothing to configure — is what
  makes this reliable; see [docs/PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md) if it still isn't.)
- **Diagnostics** (XstreamFlex → Diagnostics) comes back clean.

## Upgrading later

Bump `version` in `plugin.video.xstreamflex/addon.xml`, `python3 tools/package.py` again, and install
the new ZIP the same way as step 2. Kodi refuses to install a ZIP whose version is not higher than
what's already installed, and keeps your providers and settings across the upgrade — they live in the
profile directory, not in the add-on folder.

## Troubleshooting

- **"Install failed" with no useful detail** — almost always a ZIP built by something other than
  `tools/package.py`. Rebuild with it.
- **A source shows up under "Files" but never appears in Movies/TV Shows** — this is exactly the
  plugin-source library scanning problem section 5 above works around. Use the `.strm` sync instead
  of pointing a Kodi source directly at a `plugin://` URL.
- **Playback fails immediately, "One or more items failed to play"** — check
  [docs/PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md); this project exists largely because of exactly
  this class of problem with one specific provider, and that document has the diagnosis and the fix.
- **Port 19191 already in use** — the add-on's local playback proxy falls back to handing Kodi the
  provider's URL directly if it can't bind that port (logged as a warning, not a hard failure). Free
  the port or stop whatever else is using it if you want the proxy's reliability benefit.
