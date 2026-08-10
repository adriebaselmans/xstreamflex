# Development

## Requirements

- Python 3.9+ for the test suite (Kodi 21 Omega ships Python 3.11; the code targets 3.9 syntax so it
  also runs on older Kodi 20 builds)
- `pytest`
- Kodi 21 Omega for integration testing

The add-on itself declares `script.module.requests` as a dependency, which Kodi provides. No other
third-party packages.

## Set up

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install pytest requests
```

## Run the tests

```bash
pytest -q
```

Every provider response is a fixture in `tests/fixtures/`, including the malformed shapes real
panels return. No test contacts a provider. Two diagnostics tests do perform a DNS lookup for an
intentionally unresolvable name, so the suite is not strictly offline, but it never opens a
connection to anything real.

`tests/kodistubs.py` provides stand-ins for `xbmc`, `xbmcgui`, `xbmcplugin`, `xbmcaddon` and
`xbmcvfs`, so `tests/test_kodiui.py` and `tests/test_service.py` exercise the Kodi layer too. The
stubs are deliberately strict — the InfoTag stub accepts only setter names that genuinely exist in
Kodi 20/21, and `xbmcaddon.Addon` raises for add-ons it has not been told are installed — because a
lenient stub would let exactly the bugs it exists to catch pass through.

## The layering check

`core/` must not import Kodi modules. Verify:

```bash
! grep -rnE '^\s*(import|from)\s+xbmc' plugin.video.xstreamflex/resources/lib/core/ \
  && echo "core is Kodi-free"
```

If this fails, the tests and `tools/export_cli.py` stop working outside Kodi. See
[ARCHITECTURE.md](ARCHITECTURE.md) for why.

## Try an export without Kodi

The fastest way to validate a real provider end to end:

```bash
python3 tools/export_cli.py \
  --base http://host:8080 --user USERNAME \
  --out /tmp/channels.m3u
```

It prompts for the password — there is deliberately no `--password` flag, because an argument lands
in shell history and in the process table. Use `--password-stdin` for scripting. It fetches
categories and channels through the same `core/` code the add-on uses, writes the M3U, and prints a
summary plus the first few entries with the credentials masked. The written playlist itself
necessarily contains them, as every Xtream client's does. Add `--diagnostics` to run the full provider probe first, `--limit N` to export only the
first N categories while iterating.

## Diagnose a provider from the shell

```bash
tools/iptv-diag.sh
```

Independent of the Python code — pure `curl`. Useful as a second opinion when the add-on and the
provider disagree. Writes a log with the username and password masked, so it is safe to attach to a
bug report. Findings from the reference provider are in [PROVIDER-FINDINGS.md](PROVIDER-FINDINGS.md).

## Build an installable package

```bash
python3 tools/package.py            # -> dist/plugin.video.xstreamflex-<version>.zip
python3 tools/package.py --out /tmp
```

Kodi requires the archive to contain exactly one top-level directory named after the add-on id, with
`addon.xml` directly inside it, and it reports a violation only as a generic "invalid structure"
error. `tools/package.py` therefore verifies its own output before it finishes: top-level shape,
presence of the entry points and resources, no bytecode, and a version that matches `addon.xml`.
`tests/test_packaging.py` asserts the same properties, and additionally compiles every Python file
straight out of the archive.

Bump `version` in `plugin.video.xstreamflex/addon.xml` before building a release; Kodi refuses to
install a ZIP that is not newer than what is already installed. Installation steps for the finished
ZIP are in the [README](../README.md#install).

## Run inside Kodi

Install Kodi 21:

```bash
sudo add-apt-repository ppa:team-xbmc/ppa && sudo apt update && sudo apt install kodi
# or:  flatpak install flathub tv.kodi.Kodi
```

Symlink the add-on so edits take effect without reinstalling:

```bash
mkdir -p ~/.kodi/addons
ln -s "$PWD/plugin.video.xstreamflex" ~/.kodi/addons/plugin.video.xstreamflex
```

Start with logging on and follow it:

```bash
kodi --debug &
tail -F ~/.kodi/temp/kodi.log | grep -i xstreamflex
```

Under Flatpak the paths are `~/.var/app/tv.kodi.Kodi/data/` instead of `~/.kodi/`.

After changing `addon.xml`, `settings.xml`, or any `strings.po`, restart Kodi — those are read once
at startup. Python changes take effect on the next `plugin://` invocation, except in `service.py`,
which also needs a restart.

## Debugging notes

- `log_level=debug` in the add-on settings logs every provider request with credentials scrubbed.
  Leave it off otherwise; a full export logs hundreds of lines.
- Kodi caches add-on Python bytecode per session. If a change appears to have no effect, restart.
- A `plugin://` call that raises leaves the directory listing empty with no visible error. Check
  `kodi.log`; the router logs the traceback before Kodi swallows it.
- Testing playback repeatedly against a `max_connections: 1` account will produce spurious failures
  if a previous stream has not been torn down. Wait a few seconds between attempts.

## Style

- Standard library first, `requests` where it earns its keep, nothing else.
- Type hints on public functions; dataclasses for data.
- No logging of raw credentials, ever — everything goes through the scrubber in `core/http.py`.
- Comments explain *why*, not *what*. The Xtream quirks deserve comments; the loops do not.
