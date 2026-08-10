#!/usr/bin/env python3
"""Build an installable Kodi add-on ZIP.

Kodi is strict about the shape: the archive must contain exactly one top-level
directory whose name is the add-on id, with ``addon.xml`` directly inside it. An
archive whose files sit at the root, or whose folder carries a version suffix, is
rejected with an unhelpful error.

    python3 tools/package.py                 # -> dist/plugin.video.xstreamflex-0.1.0.zip
    python3 tools/package.py --out /tmp      # somewhere else
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_ID = "plugin.video.xstreamflex"
ADDON_DIR = os.path.join(ROOT, ADDON_ID)

#: Never ship these. Bytecode is interpreter-specific and would be stale on the
#: target; the rest is development scaffolding.
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp", "~")
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}


def addon_version(addon_xml: str) -> str:
    root = ET.parse(addon_xml).getroot()
    version = root.get("version")
    if not version:
        raise SystemExit("addon.xml has no version attribute")
    if root.get("id") != ADDON_ID:
        raise SystemExit("addon.xml id is %r, expected %r" % (root.get("id"), ADDON_ID))
    return version


def included_files(base: str):
    for directory, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
                continue
            yield os.path.join(directory, name)


def build(out_dir: str) -> str:
    addon_xml = os.path.join(ADDON_DIR, "addon.xml")
    if not os.path.exists(addon_xml):
        raise SystemExit("not found: %s" % addon_xml)
    version = addon_version(addon_xml)

    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, "%s-%s.zip" % (ADDON_ID, version))

    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in included_files(ADDON_DIR):
            # Paths inside the archive must start with the add-on id.
            arcname = os.path.join(ADDON_ID, os.path.relpath(path, ADDON_DIR))
            archive.write(path, arcname)
            count += 1

    verify(target, version)
    print("%s\n  %d files, %.1f kB, sha256 %s"
          % (target, count, os.path.getsize(target) / 1024.0, sha256(target)[:16]))
    return target


def verify(zip_path: str, version: str) -> None:
    """Fail loudly here rather than with Kodi's opaque 'invalid structure'."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        tops = {n.split("/", 1)[0] for n in names}
        if tops != {ADDON_ID}:
            raise SystemExit("archive must contain exactly one top-level dir %r, found %s"
                             % (ADDON_ID, sorted(tops)))
        required = [
            "%s/addon.xml" % ADDON_ID,
            "%s/addon.py" % ADDON_ID,
            "%s/service.py" % ADDON_ID,
            "%s/icon.png" % ADDON_ID,
            "%s/resources/settings.xml" % ADDON_ID,
            "%s/resources/lib/core/http.py" % ADDON_ID,
            "%s/resources/lib/kodiui/router.py" % ADDON_ID,
        ]
        missing = [name for name in required if name not in names]
        if missing:
            raise SystemExit("archive is missing: %s" % ", ".join(missing))
        if any(name.endswith(".pyc") for name in names):
            raise SystemExit("archive contains bytecode")

        with archive.open("%s/addon.xml" % ADDON_ID) as handle:
            if ET.parse(handle).getroot().get("version") != version:
                raise SystemExit("version mismatch inside the archive")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"),
                        help="Output directory (default: dist/)")
    args = parser.parse_args()
    build(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
