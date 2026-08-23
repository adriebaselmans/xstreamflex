import pytest

from conftest import FakeClient, load_fixture

from core.config import ProviderConfig
from core.errors import AuthError, ParseError
from core.models import LIVE
from core.providers.xtream import XtreamProvider


def make_provider(responses, **overrides):
    config = ProviderConfig(
        label="test", base_url="http://host:8080", username="u", password="p",
        user_agent="TestUA/1.0", **overrides
    )
    return XtreamProvider(config, FakeClient(responses)), config


def test_account_parses_string_numbers():
    provider, _ = make_provider({"": load_fixture("account.json")})
    account = provider.account()

    assert account.is_active
    assert account.max_connections == 1
    assert account.active_connections == 0
    assert account.allowed_output_formats == ["m3u8", "ts"]
    assert account.expires_at == 1789059188
    assert account.is_trial is False


def test_account_rejects_auth_zero():
    payload = load_fixture("account.json")
    payload["user_info"]["auth"] = 0
    provider, _ = make_provider({"": payload})

    with pytest.raises(AuthError):
        provider.account()


def test_missing_max_connections_assumes_one():
    payload = load_fixture("account.json")
    del payload["user_info"]["max_connections"]
    provider, _ = make_provider({"": payload})

    assert provider.account().max_connections == 1


def test_categories_and_channels():
    provider, _ = make_provider({
        "get_live_categories": load_fixture("live_categories.json"),
        "get_live_streams": load_fixture("live_streams.json"),
    })

    categories = provider.categories(LIVE)
    assert [c.id for c in categories] == ["1", "1161"]

    channels = provider.channels("1")
    assert channels[0].id == "843174"
    assert channels[0].name == "NPO 1 HD"
    assert channels[0].tv_archive is True
    assert channels[0].archive_days == 7
    # stream_id arrives as a number for one channel and a string for the other
    assert channels[1].id == "843175"
    assert channels[1].epg_channel_id == ""


def test_iter_channels_tags_group_from_category():
    provider, _ = make_provider({
        "get_live_categories": load_fixture("live_categories.json"),
        "get_live_streams": load_fixture("live_streams.json"),
    })

    channels = list(provider.iter_channels())
    assert len(channels) == 4  # two categories, two channels each
    assert channels[0].group == "NL | HD"
    assert channels[-1].group == "UK | Sports"


def test_iter_channels_reports_progress():
    provider, _ = make_provider({
        "get_live_categories": load_fixture("live_categories.json"),
        "get_live_streams": load_fixture("live_streams.json"),
    })
    seen = []
    list(provider.iter_channels(progress=lambda i, t, n: seen.append((i, t, n))))

    assert seen == [(1, 2, "NL | HD"), (2, 2, "UK | Sports")]


def test_empty_category_is_not_an_error():
    provider, _ = make_provider({"get_live_streams": False})
    assert provider.channels("7") == []


def test_error_object_becomes_parse_error():
    provider, _ = make_provider({"get_live_streams": {"error": "Invalid category"}})
    with pytest.raises(ParseError):
        provider.channels("7")


def test_wrapped_list_is_unwrapped():
    provider, _ = make_provider({
        "get_live_streams": {"data": load_fixture("live_streams.json")}
    })
    assert len(provider.channels("1")) == 2


def test_movies_parse():
    provider, _ = make_provider({"get_vod_streams": load_fixture("vod_streams.json")})
    movie = provider.movies("9")[0]

    assert movie.id == "555"
    assert movie.container_extension == "mkv"
    assert movie.rating == pytest.approx(7.4)
    assert movie.year == 2019


def test_series_info_sorts_episodes_and_parses_durations():
    provider, _ = make_provider({"get_series_info": load_fixture("series_info.json")})
    show, episodes = provider.series_info("42")

    assert show.name == "Some Show"
    assert show.year == 2018
    assert [(e.season, e.episode) for e in episodes] == [(1, 1), (1, 2), (2, 1)]
    assert episodes[0].duration == 2700
    # duration_secs missing, HH:MM:SS parsed instead
    assert episodes[1].duration == 2530


def test_short_epg_decodes_base64():
    provider, _ = make_provider({"get_short_epg": load_fixture("short_epg.json")})
    from core.models import Channel

    programmes = provider.short_epg(Channel(id="843174", name="NPO 1"))
    assert programmes[0].title == "Nieuws"
    assert programmes[0].description == "Het journaal"
    assert programmes[0].start == 1786752000


def test_stream_urls_have_mandatory_extension():
    provider, config = make_provider({})
    from core.models import Channel, Episode, Movie

    live = provider.live_stream(Channel(id="843174", name="x"))
    assert live.url == "http://host:8080/live/u/p/843174.ts"
    assert live.headers["User-Agent"] == "TestUA/1.0"
    assert live.mime_type == "video/mp2t"
    assert live.alternatives[0].endswith(".m3u8")

    movie = provider.movie_stream(Movie(id="555", name="m", container_extension="mkv"))
    assert movie.url == "http://host:8080/movie/u/p/555.mkv"

    episode = provider.episode_stream(
        Episode(id="9001", series_id="1", season=1, episode=1, title="t",
                container_extension="mp4"))
    assert episode.url == "http://host:8080/series/u/p/9001.mp4"


def test_preferred_format_switches_extension_and_fallback():
    provider, _ = make_provider({}, preferred_format="m3u8")
    from core.models import Channel

    ref = provider.live_stream(Channel(id="1", name="x"))
    assert ref.url.endswith(".m3u8")
    assert ref.mime_type == ""
    assert ref.alternatives[0].endswith(".ts")


def test_credentials_are_url_quoted():
    provider, _ = make_provider({})
    provider.config.username = "user name"
    provider.config.password = "p@ss/word"
    from core.models import Channel

    url = provider.live_stream(Channel(id="1", name="x")).url
    assert url == "http://host:8080/live/user%20name/p%40ss%2Fword/1.ts"


def test_get_php_is_never_called():
    """The whole point of the project: this client must not touch get.php."""
    client = FakeClient({
        "get_live_categories": load_fixture("live_categories.json"),
        "get_live_streams": load_fixture("live_streams.json"),
        "": load_fixture("account.json"),
    })
    config = ProviderConfig(label="t", base_url="http://host:8080",
                            username="u", password="p")
    provider = XtreamProvider(config, client)

    provider.account()
    list(provider.iter_channels())

    assert client.calls, "expected the provider to make requests"
    assert all("get.php" not in url for url, _, _ in client.calls)
    assert all(url.endswith("/player_api.php") for url, _, _ in client.calls)


def test_movie_info_is_cached_far_longer_than_series_info():
    """A film's facts never change, so re-asking costs 12 minutes of provider
    calls for nothing. A *show* gains episodes, so it must keep expiring - the
    two must not share a TTL.
    """
    import inspect

    from core.cache import TTL_IMMUTABLE, TTL_METADATA
    from core.providers import xtream

    assert TTL_IMMUTABLE > TTL_METADATA * 50

    assert "TTL_IMMUTABLE" in inspect.getsource(xtream.XtreamProvider.movie_info)
    assert "TTL_METADATA" in inspect.getsource(xtream.XtreamProvider.series_info)
    assert "TTL_IMMUTABLE" not in inspect.getsource(xtream.XtreamProvider.series_info)
