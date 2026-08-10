"""Regressions for defects found in the independent review.

Each test here corresponds to a bug that shipped in the first draft and would not
have been caught by the original suite.
"""
import os
import xml.etree.ElementTree as ET

import pytest

from core.config import ProviderConfig
from core.errors import ConnectionLimitError
from core.export.exporter import export_lock
from core.export.iptvsimple import apply_plan, build_plan
from core.export.m3u_writer import write_m3u
from core.models import Channel
from core.providers.m3u import parse_m3u, split_extinf


# -- IPTV Simple: Kodi resets settings that still carry default="true" ----

def write_instance(root, settings):
    directory = os.path.join(root, "pvr.iptvsimple")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "instance-settings-1.xml")
    element = ET.Element("settings", version="2")
    for key, (value, is_default) in settings.items():
        child = ET.SubElement(element, "setting")
        child.set("id", key)
        if is_default:
            child.set("default", "true")
        child.text = value
    ET.ElementTree(element).write(path, encoding="utf-8", xml_declaration=True)
    return path


def test_default_attribute_is_removed_from_written_settings(tmp_path):
    """Kodi calls Reset() on elements marked default="true" after reading them.

    Leaving the attribute means Kodi discards our value, m3uPathType snaps back to
    'remote URL', and the exported playlist is never read — while the add-on has
    already told the user it configured everything.
    """
    root = str(tmp_path)
    path = write_instance(root, {
        "m3uPathType": ("1", True),
        "epgPathType": ("1", True),
        "defaultUserAgent": ("", True),
    })

    plan = apply_plan(build_plan("/data/ch.m3u", "http://e/x.php", "UA/1.0"), root)
    assert plan.applied is True

    written = {e.get("id"): e for e in ET.parse(path).getroot().findall("setting")}
    for key in ("m3uPathType", "epgPathType", "defaultUserAgent", "m3uPath"):
        assert "default" not in written[key].attrib, key
    assert written["m3uPathType"].text == "0"


def test_untouched_settings_keep_their_default_attribute(tmp_path):
    root = str(tmp_path)
    path = write_instance(root, {
        "m3uPathType": ("1", True),
        "somethingElse": ("42", True),
    })

    apply_plan(build_plan("/data/ch.m3u", "", "UA"), root)

    written = {e.get("id"): e for e in ET.parse(path).getroot().findall("setting")}
    assert written["somethingElse"].attrib.get("default") == "true"


# -- M3U parsing: commas inside quoted attribute values -------------------

def test_comma_inside_attribute_value_does_not_eat_the_name():
    line = ('#EXTINF:-1 tvg-id="a.uk" tvg-name="BBC, One" tvg-logo="http://x/l.png" '
            'group-title="UK, HD",BBC One HD')
    channels = list(parse_m3u(["#EXTM3U", line, "http://host/1.ts"]))

    channel = channels[0]
    assert channel.name == "BBC, One"
    assert channel.logo == "http://x/l.png"
    assert channel.group == "UK, HD"
    assert channel.direct_source == "http://host/1.ts"


def test_comma_in_display_name_is_preserved():
    line = '#EXTINF:-1 tvg-id="x",Movies, Series and More'
    duration, attrs, name = split_extinf(line)

    assert duration == "-1"
    assert name == "Movies, Series and More"
    assert 'tvg-id="x"' in attrs


def test_split_extinf_rejects_a_line_without_a_comma():
    assert split_extinf("#EXTINF:-1 tvg-id=\"x\"") is None


def test_split_extinf_rejects_a_missing_duration():
    assert split_extinf("#EXTINF:broken,Name") is None


def test_fractional_duration_is_accepted():
    assert split_extinf("#EXTINF:12.5,Name")[0] == "12.5"


# -- Exporter numbering and tvg-id ---------------------------------------

def channel(**kwargs):
    base = dict(id="1", name="One", category_id="1", number=0, logo="",
                epg_channel_id="", group="G")
    base.update(kwargs)
    return Channel(**base)


def test_per_category_numbers_do_not_collide(tmp_path):
    """Xtream's `num` restarts at 1 in every category; the export must not."""
    out = str(tmp_path / "a.m3u")
    channels = [
        channel(id="1", number=1, group="NL"),
        channel(id="2", number=2, group="NL"),
        channel(id="3", number=1, group="UK"),
        channel(id="4", number=2, group="UK"),
    ]
    write_m3u(out, channels, lambda c: "http://h/%s.ts" % c.id, user_agent="UA")

    numbers = [line.split('tvg-chno="')[1].split('"')[0]
               for line in open(out, encoding="utf-8") if "tvg-chno" in line]
    assert len(numbers) == len(set(numbers)), numbers


def test_channel_without_epg_id_gets_an_empty_tvg_id(tmp_path):
    """A synthetic tvg-id matches nothing in XMLTV and blocks name matching."""
    out = str(tmp_path / "b.m3u")
    write_m3u(out, [channel(id="843175", epg_channel_id="")],
              lambda c: "http://h/1.ts", user_agent="UA")

    extinf = [l for l in open(out, encoding="utf-8") if l.startswith("#EXTINF")][0]
    assert 'tvg-id=""' in extinf
    assert "843175" not in extinf


def test_real_epg_id_is_kept(tmp_path):
    out = str(tmp_path / "c.m3u")
    write_m3u(out, [channel(epg_channel_id="npo1.nl")],
              lambda c: "http://h/1.ts", user_agent="UA")
    assert 'tvg-id="npo1.nl"' in open(out, encoding="utf-8").read()


# -- Cross-process export lock -------------------------------------------

def test_second_export_is_refused_while_one_runs(tmp_path):
    directory = str(tmp_path)
    with export_lock(directory):
        with pytest.raises(ConnectionLimitError):
            with export_lock(directory):
                pass


def test_lock_is_released_afterwards(tmp_path):
    directory = str(tmp_path)
    with export_lock(directory):
        pass
    with export_lock(directory):
        pass  # must not raise


def test_lock_is_released_when_the_body_raises(tmp_path):
    directory = str(tmp_path)
    with pytest.raises(RuntimeError):
        with export_lock(directory):
            raise RuntimeError("boom")
    with export_lock(directory):
        pass


# -- Credentials in the EPG URL ------------------------------------------

def test_xmltv_url_quotes_special_characters():
    config = ProviderConfig(base_url="http://host:8080", username="user name",
                            password="p&ss+word#1")
    url = config.xmltv_url

    assert " " not in url
    assert "&password=" in url
    assert "p%26ss%2Bword%231" in url
