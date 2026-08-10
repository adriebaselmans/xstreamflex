"""The installable ZIP.

Kodi rejects a badly shaped archive with an error that names nothing useful, so the
shape is asserted here instead.
"""
import importlib.util
import os
import xml.etree.ElementTree as ET
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_ID = "plugin.video.xstreamflex"


def load_packager():
    path = os.path.join(ROOT, "tools", "package.py")
    spec = importlib.util.spec_from_file_location("package_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    packager = load_packager()
    out = str(tmp_path_factory.mktemp("dist"))
    return packager.build(out)


def names(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        return archive.namelist()


def test_filename_carries_id_and_version(built):
    addon_xml = os.path.join(ROOT, ADDON_ID, "addon.xml")
    version = ET.parse(addon_xml).getroot().get("version")
    assert os.path.basename(built) == "%s-%s.zip" % (ADDON_ID, version)


def test_single_top_level_directory_named_after_the_addon(built):
    tops = {name.split("/", 1)[0] for name in names(built)}
    assert tops == {ADDON_ID}


def test_addon_xml_sits_directly_inside_that_directory(built):
    assert "%s/addon.xml" % ADDON_ID in names(built)


def test_entry_points_and_resources_are_present(built):
    present = set(names(built))
    for required in (
        "addon.py", "service.py", "icon.png", "fanart.jpg",
        "resources/settings.xml",
        "resources/language/resource.language.en_gb/strings.po",
        "resources/language/resource.language.nl_nl/strings.po",
        "resources/lib/core/http.py",
        "resources/lib/core/providers/xtream.py",
        "resources/lib/core/export/m3u_writer.py",
        "resources/lib/kodiui/router.py",
    ):
        assert "%s/%s" % (ADDON_ID, required) in present, required


def test_no_bytecode_or_development_scaffolding(built):
    for name in names(built):
        assert not name.endswith(".pyc"), name
        assert "__pycache__" not in name, name
        assert "/tests/" not in name, name


def test_every_python_file_compiles_from_the_archive(built, tmp_path):
    """A packaged file that cannot compile is a broken install, not a broken test."""
    import py_compile

    with zipfile.ZipFile(built) as archive:
        archive.extractall(str(tmp_path))
    extracted = os.path.join(str(tmp_path), ADDON_ID)

    for directory, _, filenames in os.walk(extracted):
        for name in filenames:
            if name.endswith(".py"):
                py_compile.compile(os.path.join(directory, name), doraise=True)


def test_verify_rejects_a_flat_archive(tmp_path):
    packager = load_packager()
    bad = str(tmp_path / "flat.zip")
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("addon.xml", "<addon/>")

    with pytest.raises(SystemExit):
        packager.verify(bad, "0.1.0")
