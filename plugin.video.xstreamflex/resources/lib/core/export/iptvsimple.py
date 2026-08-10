"""Hooking the exported playlist up to IPTV Simple.

Since Kodi 20, IPTV Simple keeps its configuration in per-instance settings files
(``instance-settings-<n>.xml``); the ids in the add-on's own ``settings.xml`` are
marked ``hidden_obsolete`` and retained only for migration.

Writing another add-on's private settings file is a convenience, never a dependency.
If anything about the instance layout is unclear, we hand the user the exact values
to type instead. A silent misconfiguration would be far worse than one manual step.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

ADDON_ID = "pvr.iptvsimple"

# Verified against the Omega branch of pvr.iptvsimple.
SETTING_M3U_PATH_TYPE = "m3uPathType"      # 0 = local path, 1 = remote URL
SETTING_M3U_PATH = "m3uPath"
SETTING_EPG_PATH_TYPE = "epgPathType"
SETTING_EPG_URL = "epgUrl"
SETTING_USER_AGENT = "defaultUserAgent"
SETTING_REFRESH_MODE = "m3uRefreshMode"    # 1 = repeated interval
SETTING_REFRESH_INTERVAL = "m3uRefreshIntervalMins"

_INSTANCE_RE = re.compile(r"^instance-settings-(\d+)\.xml$")


@dataclass
class SetupPlan:
    """What IPTV Simple needs, and whether we could apply it ourselves."""

    values: Dict[str, str] = field(default_factory=dict)
    instance_file: str = ""
    applied: bool = False
    reason: str = ""
    candidates: List[str] = field(default_factory=list)

    def as_instructions(self) -> List[str]:
        labels = [
            ("Playlist location", "Local path"),
            ("M3U play list path", self.values.get(SETTING_M3U_PATH, "")),
            ("EPG location", "Remote path (URL)"),
            ("XMLTV URL", self.values.get(SETTING_EPG_URL, "")),
            ("Default user agent", self.values.get(SETTING_USER_AGENT, "")),
            ("Refresh mode", "Repeated interval"),
            ("Refresh interval", "%s minutes" % self.values.get(SETTING_REFRESH_INTERVAL, "60")),
        ]
        return ["%s: %s" % (name, value) for name, value in labels if value]


def build_plan(m3u_path: str, epg_url: str, user_agent: str,
               refresh_minutes: int = 60) -> SetupPlan:
    values = {
        SETTING_M3U_PATH_TYPE: "0",
        SETTING_M3U_PATH: m3u_path,
        SETTING_REFRESH_MODE: "1",
        SETTING_REFRESH_INTERVAL: str(max(5, int(refresh_minutes))),
    }
    if epg_url:
        # The provider's XMLTV feed is healthy and fast, so IPTV Simple fetches it
        # directly. Proxying it would add a failure mode and 30 MB of disk churn.
        values[SETTING_EPG_PATH_TYPE] = "1"
        values[SETTING_EPG_URL] = epg_url
    if user_agent:
        values[SETTING_USER_AGENT] = user_agent
    return SetupPlan(values=values)


def find_instance_files(addon_data_root: str) -> List[str]:
    """List IPTV Simple's per-instance settings files, lowest instance first."""
    directory = os.path.join(addon_data_root, ADDON_ID)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = []
    for name in names:
        match = _INSTANCE_RE.match(name)
        if match:
            found.append((int(match.group(1)), os.path.join(directory, name)))
    return [path for _, path in sorted(found)]


def apply_plan(plan: SetupPlan, addon_data_root: str,
               instance_file: Optional[str] = None) -> SetupPlan:
    """Write the plan into an IPTV Simple instance settings file.

    Refuses rather than guesses when there is no instance, or more than one and the
    caller did not pick.
    """
    candidates = find_instance_files(addon_data_root)
    plan.candidates = candidates

    target = instance_file
    if target is None:
        if not candidates:
            plan.reason = (
                "IPTV Simple has no saved configuration yet. Open its settings once, "
                "then run this again — or enter the values manually."
            )
            return plan
        if len(candidates) > 1:
            plan.reason = (
                "IPTV Simple has %d configurations. Pick which one to change, or "
                "enter the values manually." % len(candidates)
            )
            return plan
        target = candidates[0]

    try:
        tree = ET.parse(target)
        root = tree.getroot()
    except (OSError, ET.ParseError) as exc:
        plan.reason = "Could not read %s (%s)." % (os.path.basename(target), exc)
        return plan

    existing = {}
    for element in root.findall("setting"):
        setting_id = element.get("id")
        if setting_id:
            existing[setting_id] = element

    for key, value in plan.values.items():
        element = existing.get(key)
        if element is None:
            element = ET.SubElement(root, "setting")
            element.set("id", key)
        element.text = value
        # Kodi stamps default="true" on any setting still at its schema default, and
        # on load it calls Reset() on those elements *after* reading the value back.
        # Leaving the attribute in place means Kodi silently discards everything we
        # just wrote — which is the common case, because these settings are all at
        # their defaults until someone touches them by hand.
        element.attrib.pop("default", None)

    backup = target + ".xstreamflex-backup"
    try:
        if not os.path.exists(backup):
            # Keep one pristine copy: this is another add-on's configuration and the
            # user must be able to get back to it.
            with open(target, "rb") as src, open(backup, "wb") as dst:
                dst.write(src.read())
        tmp = target + ".tmp"
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, target)
    except OSError as exc:
        plan.reason = "Could not write %s (%s)." % (os.path.basename(target), exc)
        return plan

    plan.instance_file = target
    plan.applied = True
    plan.reason = "Restart Kodi for IPTV Simple to pick up the new configuration."
    return plan
