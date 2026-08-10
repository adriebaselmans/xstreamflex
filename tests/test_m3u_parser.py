from core.providers.m3u import parse_m3u

PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="one.nl" tvg-name="One" tvg-chno="1" tvg-logo="http://l/1.png" group-title="NL",One HD
#EXTVLCOPT:http-user-agent=CustomUA/2.0
#EXTVLCOPT:http-referrer=http://ref/
http://host/live/u/p/1.ts
#EXTINF:-1 tvg-id="two.nl" group-title="NL",Two HD
#KODIPROP:inputstream=inputstream.adaptive
#KODIPROP:inputstream.adaptive.manifest_type=hls
http://host/live/u/p/2.m3u8
#EXTINF:-1,No attributes
#EXTGRP:Extra
http://host/live/u/p/3.ts
"""


def parse(text, **kwargs):
    return list(parse_m3u(text.splitlines(), **kwargs))


def test_attributes_and_headers():
    channels = parse(PLAYLIST, default_user_agent="DefaultUA")

    assert len(channels) == 3
    first = channels[0]
    assert first.id == "one.nl"
    assert first.name == "One"
    assert first.number == 1
    assert first.logo == "http://l/1.png"
    assert first.group == "NL"
    assert first.headers["User-Agent"] == "CustomUA/2.0"
    assert first.headers["Referer"] == "http://ref/"
    assert first.direct_source == "http://host/live/u/p/1.ts"


def test_kodiprops_are_passed_through():
    channels = parse(PLAYLIST)
    assert channels[1].kodi_props["inputstream"] == "inputstream.adaptive"
    assert channels[1].kodi_props["inputstream.adaptive.manifest_type"] == "hls"


def test_default_user_agent_applied_when_absent():
    channels = parse(PLAYLIST, default_user_agent="DefaultUA")
    assert channels[1].headers["User-Agent"] == "DefaultUA"


def test_extgrp_is_used_when_group_title_missing():
    channels = parse(PLAYLIST)
    assert channels[2].group == "Extra"


def test_missing_tvg_id_gets_a_stable_synthetic_id():
    first = parse(PLAYLIST)[2].id
    second = parse(PLAYLIST)[2].id
    assert first == second
    assert first.startswith("m3u")


def test_state_does_not_leak_between_entries():
    """A per-channel option must not stick to the next channel."""
    channels = parse(PLAYLIST, default_user_agent="DefaultUA")
    assert "Referer" not in channels[1].headers
    assert channels[2].kodi_props == {}


def test_malformed_extinf_is_skipped_without_crashing():
    text = "#EXTM3U\n#EXTINF:broken\nhttp://host/x.ts\n#EXTINF:-1,Good\nhttp://host/y.ts\n"
    channels = parse(text)
    assert [c.name for c in channels] == ["Good"]


def test_comment_lines_are_ignored():
    text = '#EXTM3U\n# a comment\n#EXTINF:-1,Name\n#EXTUNKNOWN:x\nhttp://host/z.ts\n'
    channels = parse(text)
    assert len(channels) == 1
    assert channels[0].direct_source == "http://host/z.ts"
