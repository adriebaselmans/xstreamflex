import os
import stat

from core.export.m3u_writer import write_m3u
from core.models import Channel


def channel(**kwargs):
    base = dict(id="1", name="Channel One", category_id="1", number=0,
                logo="http://logo/1.png", epg_channel_id="one.nl", group="NL | HD")
    base.update(kwargs)
    return Channel(**base)


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def test_writes_user_agent_for_every_channel(tmp_path):
    out = str(tmp_path / "channels.m3u")
    result = write_m3u(out, [channel(), channel(id="2", name="Channel Two")],
                       lambda c: "http://host/live/u/p/%s.ts" % c.id,
                       user_agent="TestUA/1.0")

    text = read(out)
    # A provider that answers 454 without a UA refuses every channel, so the
    # option must appear once per entry, not once per file.
    assert text.count('#EXTVLCOPT:http-user-agent="TestUA/1.0"') == 2
    assert result.channel_count == 2
    assert result.group_count == 1


def test_user_agent_with_spaces_is_quoted(tmp_path):
    """IPTV Simple's own M3U parser reads an EXTVLCOPT value up to the first
    space unless it is double-quoted, silently truncating a bare UA string."""
    out = str(tmp_path / "channels.m3u")
    write_m3u(out, [channel()], lambda c: "http://host/live/u/p/1.ts",
              user_agent="Mozilla/5.0 (Linux; Android 12) Chrome/120.0")

    text = read(out)
    assert '#EXTVLCOPT:http-user-agent="Mozilla/5.0 (Linux; Android 12) Chrome/120.0"' in text


def test_ts_gets_mimetype_hls_does_not(tmp_path):
    out = str(tmp_path / "a.m3u")
    write_m3u(out, [channel()], lambda c: "http://host/live/u/p/1.ts",
              user_agent="UA")
    assert "#KODIPROP:mimetype=video/mp2t" in read(out)

    out2 = str(tmp_path / "b.m3u")
    write_m3u(out2, [channel()], lambda c: "http://host/live/u/p/1.m3u8",
              user_agent="UA")
    assert "mimetype" not in read(out2)


def test_duplicate_stream_ids_are_dropped(tmp_path):
    out = str(tmp_path / "c.m3u")
    result = write_m3u(
        out,
        [channel(), channel(group="UK | Sports"), channel(id="2")],
        lambda c: "http://host/%s.ts" % c.id,
        user_agent="UA",
    )
    assert result.channel_count == 2


def test_channels_without_id_or_url_are_reported(tmp_path):
    out = str(tmp_path / "d.m3u")
    result = write_m3u(
        out,
        [channel(id="", name="No id"), channel(id="3", name="No url")],
        lambda c: "" if c.id == "3" else "http://host/x.ts",
        user_agent="UA",
    )
    assert result.channel_count == 0
    assert len(result.skipped) == 2
    assert "No id" in result.skipped[0]


def test_attribute_quotes_do_not_break_the_line(tmp_path):
    out = str(tmp_path / "e.m3u")
    write_m3u(out, [channel(name='He said "hi"', group='Group "A"')],
              lambda c: "http://host/1.ts", user_agent="UA")

    extinf = [line for line in read(out).splitlines() if line.startswith("#EXTINF")][0]
    assert extinf.count('"') % 2 == 0
    assert 'tvg-name="He said \'hi\'"' in extinf


def test_numbering_falls_back_to_sequence(tmp_path):
    out = str(tmp_path / "f.m3u")
    write_m3u(out, [channel(number=0), channel(id="2", number=77)],
              lambda c: "http://host/%s.ts" % c.id, user_agent="UA")

    text = read(out)
    assert 'tvg-chno="1"' in text
    assert 'tvg-chno="77"' in text


def test_renumber_overrides_provider_numbers(tmp_path):
    out = str(tmp_path / "g.m3u")
    write_m3u(out, [channel(number=50), channel(id="2", number=77)],
              lambda c: "http://host/%s.ts" % c.id, user_agent="UA", renumber=True)

    text = read(out)
    assert 'tvg-chno="1"' in text and 'tvg-chno="2"' in text


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    out = str(tmp_path / "h.m3u")
    write_m3u(out, [channel()], lambda c: "http://host/1.ts", user_agent="UA")

    leftovers = [n for n in os.listdir(str(tmp_path)) if n.startswith(".xstreamflex-")]
    assert leftovers == []


def test_playlist_is_not_world_readable(tmp_path):
    out = str(tmp_path / "i.m3u")
    write_m3u(out, [channel()], lambda c: "http://host/u/p/1.ts", user_agent="UA")

    mode = stat.S_IMODE(os.stat(out).st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0


def test_starts_with_extm3u(tmp_path):
    out = str(tmp_path / "j.m3u")
    write_m3u(out, [], lambda c: "", user_agent="UA")
    assert read(out) == "#EXTM3U\n"


# -- country filtering in export_channels ------------------------------------

class _FakeProvider:
    """Minimal stand-in for the Xtream provider: only iter_channels is used."""

    kind = "xtream"

    def __init__(self, channels):
        self._channels = channels

    def iter_channels(self, progress=None):
        return iter(self._channels)

    def live_url(self, channel_id):
        return "http://host/live/u/p/%s.ts" % channel_id


def _config():
    from core.config import ProviderConfig
    return ProviderConfig(label="T", base_url="http://host:8080",
                          username="u", password="p")


def _panel():
    """A slice shaped like the real panel: mostly foreign, a few Dutch groups,
    including the provider's inconsistent double-space spelling."""
    return [
        channel(id="1", name="NPO 1", group="NL | NEDERLAND ALL"),
        channel(id="2", name="Apple film", group="NL  | APPLE TV+ FILMS"),
        channel(id="3", name="CNN", group="US | USA"),
        channel(id="4", name="Sport1", group="DE | SPORTDEUTSCHLAND TV"),
        channel(id="5", name="NLD kanaal", group="NLD | NOT DUTCH"),
    ]


def test_country_filter_keeps_only_matching_groups(tmp_path):
    from core.export.exporter import export_channels

    out = str(tmp_path / "channels.m3u")
    result = export_channels(_FakeProvider(_panel()), _config(), out, country="NL")

    text = read(out)
    assert result.channel_count == 2, "only the two NL groups should survive"
    assert "NPO 1" in text and "Apple film" in text
    assert "CNN" not in text and "Sport1" not in text
    # "NLD" starts with the same letters but is a different country.
    assert "NLD kanaal" not in text


def test_no_country_means_everything(tmp_path):
    """The "all countries" menu entry passes an empty country."""
    from core.export.exporter import export_channels

    out = str(tmp_path / "channels.m3u")
    result = export_channels(_FakeProvider(_panel()), _config(), out, country="")

    assert result.channel_count == 5


def test_country_filter_falls_back_to_category_id(tmp_path):
    """Channels whose group is empty still carry the category name."""
    from core.export.exporter import export_channels

    out = str(tmp_path / "channels.m3u")
    channels = [
        channel(id="1", name="Keep", group="", category_id="NL | RADIO"),
        channel(id="2", name="Drop", group="", category_id="US | USA"),
    ]
    result = export_channels(_FakeProvider(channels), _config(), out, country="NL")

    assert result.channel_count == 1
    assert "Keep" in read(out)
