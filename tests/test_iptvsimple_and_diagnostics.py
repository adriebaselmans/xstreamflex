import os
import xml.etree.ElementTree as ET

from core.config import ProviderConfig
from core.diagnostics import FAIL, OK, run_diagnostics
from core.export.iptvsimple import (
    SETTING_EPG_URL,
    SETTING_M3U_PATH,
    SETTING_M3U_PATH_TYPE,
    SETTING_USER_AGENT,
    apply_plan,
    build_plan,
    find_instance_files,
)


def make_instance(root, number=1, existing=None):
    directory = os.path.join(root, "pvr.iptvsimple")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "instance-settings-%d.xml" % number)
    settings = ET.Element("settings", version="2")
    for key, value in (existing or {"m3uPathType": "1", "m3uUrl": "http://old/get.php"}).items():
        element = ET.SubElement(settings, "setting")
        element.set("id", key)
        element.text = value
    ET.ElementTree(settings).write(path, encoding="utf-8", xml_declaration=True)
    return path


def read_settings(path):
    root = ET.parse(path).getroot()
    return {e.get("id"): (e.text or "") for e in root.findall("setting")}


def test_plan_uses_local_m3u_and_remote_epg():
    plan = build_plan("/data/channels.m3u", "http://host/xmltv.php", "UA/1.0")

    assert plan.values[SETTING_M3U_PATH_TYPE] == "0"
    assert plan.values[SETTING_M3U_PATH] == "/data/channels.m3u"
    assert plan.values["epgPathType"] == "1"
    assert plan.values[SETTING_EPG_URL] == "http://host/xmltv.php"
    assert plan.values[SETTING_USER_AGENT] == "UA/1.0"


def test_plan_without_epg_leaves_epg_settings_alone():
    plan = build_plan("/data/channels.m3u", "", "UA/1.0")
    assert SETTING_EPG_URL not in plan.values
    assert "epgPathType" not in plan.values


def test_refresh_interval_has_a_floor():
    assert build_plan("/p", "", "UA", refresh_minutes=1).values["m3uRefreshIntervalMins"] == "5"


def test_apply_updates_existing_and_adds_missing(tmp_path):
    root = str(tmp_path)
    path = make_instance(root)

    plan = apply_plan(build_plan("/data/ch.m3u", "http://e/x.php", "UA/1.0"), root)

    assert plan.applied is True
    values = read_settings(path)
    assert values["m3uPathType"] == "0"          # existing setting changed
    assert values["m3uPath"] == "/data/ch.m3u"   # missing setting added
    assert values["m3uUrl"] == "http://old/get.php"  # unrelated setting untouched


def test_apply_makes_one_backup(tmp_path):
    root = str(tmp_path)
    path = make_instance(root)

    apply_plan(build_plan("/a", "", "UA"), root)
    backup = path + ".xstreamflex-backup"
    assert os.path.exists(backup)
    assert "m3uUrl" in read_settings(backup)

    first = open(backup, "rb").read()
    apply_plan(build_plan("/b", "", "UA"), root)
    # The backup keeps the pristine original, not the previous run's output.
    assert open(backup, "rb").read() == first


def test_apply_refuses_when_no_instance_exists(tmp_path):
    plan = apply_plan(build_plan("/a", "", "UA"), str(tmp_path))
    assert plan.applied is False
    assert "no saved configuration" in plan.reason


def test_apply_refuses_to_guess_between_instances(tmp_path):
    root = str(tmp_path)
    make_instance(root, 1)
    make_instance(root, 2)

    plan = apply_plan(build_plan("/a", "", "UA"), root)
    assert plan.applied is False
    assert len(plan.candidates) == 2


def test_instance_files_are_sorted_numerically(tmp_path):
    root = str(tmp_path)
    make_instance(root, 10)
    make_instance(root, 2)
    names = [os.path.basename(p) for p in find_instance_files(root)]
    assert names == ["instance-settings-2.xml", "instance-settings-10.xml"]


def test_instructions_are_offered_when_not_applied(tmp_path):
    plan = apply_plan(build_plan("/data/ch.m3u", "http://e/x", "UA"), str(tmp_path))
    text = "\n".join(plan.as_instructions())
    assert "/data/ch.m3u" in text
    assert "Local path" in text


def test_diagnostics_reports_unresolvable_host():
    config = ProviderConfig(base_url="http://does-not-exist.invalid:8080",
                            username="u", password="p")

    class NoClient:
        def probe(self, *a, **kw):
            raise AssertionError("should not get this far")

    report = run_diagnostics(config, NoClient())
    assert report.failed
    assert report.checks[0].status == FAIL


def test_diagnostics_reports_missing_server_url():
    report = run_diagnostics(ProviderConfig(), None)
    assert report.checks[0].name == "Server address"
    assert report.checks[0].status == FAIL


def test_report_text_contains_verdict():
    report = run_diagnostics(ProviderConfig(), None)
    assert "Verdict:" in report.as_text()
    assert report.verdict != OK
