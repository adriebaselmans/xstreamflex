import os

from core.library_sync import (
    _MAX_PATH,
    clean_title,
    episode_strm_content,
    episode_strm_path,
    is_sync_stale,
    last_sync_time,
    matches_country,
    movie_nfo_content,
    movie_nfo_path,
    movie_strm_content,
    movie_strm_path,
    safe_filename,
    show_nfo_content,
    show_nfo_path,
    sync_episodes,
    sync_movies,
    write_sync_state,
)
from core.models import Episode, Movie, Series


def movie(**kwargs):
    base = dict(id="1", name="Some Movie (NL)", container_extension="mkv")
    base.update(kwargs)
    return Movie(**base)


def episode(**kwargs):
    base = dict(id="10", series_id="1", season=1, episode=2, title="The Pilot",
                container_extension="mkv")
    base.update(kwargs)
    return Episode(**base)


def series(**kwargs):
    base = dict(id="1", name="Show One")
    base.update(kwargs)
    return Series(**base)


def test_safe_filename_strips_illegal_characters():
    assert safe_filename('Bad: Name / With * Junk?') == "Bad Name With Junk"


def test_safe_filename_falls_back_when_nothing_survives():
    assert safe_filename("???", fallback="x") == "x"


def test_safe_filename_strips_trailing_dots_and_spaces():
    assert safe_filename("Trailing dot. ") == "Trailing dot"


def test_movie_strm_content_points_at_play_movie():
    content = movie_strm_content("plugin://plugin.video.xstreamflex/", movie())
    assert content == (
        "plugin://plugin.video.xstreamflex?action=play_movie&movie_id=1&ext=mkv"
        "&title=Some%20Movie%20%28NL%29"
    )


def test_episode_strm_content_points_at_play_episode():
    content = episode_strm_content("plugin://plugin.video.xstreamflex/", episode())
    assert content == (
        "plugin://plugin.video.xstreamflex?action=play_episode&episode_id=10&ext=mkv"
        "&title=The%20Pilot"
    )


def test_episode_strm_path_nests_directly_under_the_show_no_source_folder(tmp_path):
    """Two things deliberately not in the filename/path, both confirmed against
    a real account: the show name is not repeated in the filename (Kodi's TV
    scanner already gets it from the parent folder, and repeating it was a
    main contributor to filenames exceeding Windows' MAX_PATH), and there is
    no source folder between the library root and the show (unlike movies) -
    Kodi's TV scanner expects exactly one level of nesting and does not
    descend past an unrecognized folder to find the real show folders under
    it, which left virtually the entire series catalogue invisible when a
    source folder was tried."""
    root = str(tmp_path)
    path = episode_strm_path(root, "NL | Netflix", "Turner & Hooch", episode())
    assert path == os.path.join(root, "Turner & Hooch", "S01E02 - The Pilot (10).strm")


def test_episode_strm_path_strips_illegal_characters_from_the_show_name(tmp_path):
    root = str(tmp_path)
    path = episode_strm_path(root, "NL | Netflix", "Show: Part Two", episode())
    assert os.path.basename(os.path.dirname(path)) == "Show Part Two"


def test_movie_strm_path_nests_under_source(tmp_path):
    root = str(tmp_path)
    path = movie_strm_path(root, "NL | Videoland", movie())
    assert path == os.path.join(root, "NL Videoland", "Some Movie (NL) (1).strm")


# -- NFO generation: tags for browsing by source, self-contained metadata ----

def test_movie_nfo_path_matches_the_strm_basename(tmp_path):
    root = str(tmp_path)
    strm = movie_strm_path(root, "Src", movie())
    nfo = movie_nfo_path(root, "Src", movie())
    assert nfo == strm[: -len(".strm")] + ".nfo"


def test_movie_nfo_content_has_title_and_source_tag():
    content = movie_nfo_content(movie(), "NL | Videoland")
    assert "<title>Some Movie</title>" in content, "the (NL) marker is not a title"
    assert "<tag>NL | Videoland</tag>" in content
    assert content.startswith('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')


def test_movie_nfo_content_includes_optional_fields_when_present():
    m = movie(plot="A plot.", year=2024, genre="Action", rating=7.5, icon="http://img/p.jpg")
    content = movie_nfo_content(m, "Src", headers={"User-Agent": "UA/1.0"})
    assert "<plot>A plot.</plot>" in content
    assert "<year>2024</year>" in content
    assert "<genre>Action</genre>" in content
    assert "<rating>7.5</rating>" in content
    assert "http://img/p.jpg|User-Agent=UA%2F1.0" in content


def test_movie_nfo_content_omits_optional_fields_when_absent():
    content = movie_nfo_content(movie(), "Src")
    assert "<plot>" not in content
    assert "<year>" not in content
    assert "<thumb" not in content


def test_movie_nfo_content_escapes_xml_special_characters():
    content = movie_nfo_content(movie(name="Tom & Jerry <Movie>"), "Src")
    assert "Tom &amp; Jerry &lt;Movie&gt;" in content
    assert "<Movie>" not in content


def test_show_nfo_path_is_tvshow_nfo_in_the_show_folder(tmp_path):
    root = str(tmp_path)
    assert show_nfo_path(root, "Turner & Hooch") == os.path.join(
        root, "Turner & Hooch", "tvshow.nfo")


def test_show_nfo_content_has_title_and_source_tag():
    content = show_nfo_content(series(name="Show One"), "NL | Netflix")
    assert "<title>Show One</title>" in content
    assert "<tag>NL | Netflix</tag>" in content
    assert content.startswith('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    assert "<tvshow>" in content and "</tvshow>" in content


def test_show_nfo_content_includes_cover_as_poster_thumb():
    content = show_nfo_content(series(cover="http://img/cover.jpg"), "Src",
                               headers={"User-Agent": "UA"})
    assert '<thumb aspect="poster">http://img/cover.jpg|User-Agent=UA</thumb>' in content


# -- the actual bug: a provider-formatted title long enough to break Windows --
#
# A realistic root length is used (~110 chars) rather than an arbitrarily deep
# one: once root plus the fixed parts (source/show folder names, the "S01E02"
# prefix, the "(id).strm" suffix) alone already leave no budget, no amount of
# shrinking the free-text title can help - that is an inherent limit of
# available path length, not something this module can paper over. What it can
# and does guarantee is that a long *title*, on a realistic root, no longer
# blows the budget the way the real provider's doubled titles did.

def test_movie_strm_path_never_exceeds_the_safe_path_length_with_a_realistic_root():
    realistic_root = "C:\\Users\\Someone\\AppData\\Roaming\\Kodi\\userdata\\addon_data\\" \
                     "plugin.video.xstreamflex\\library\\movies"
    long_title = "A " * 200  # 400 chars, far beyond any reasonable filename
    path = movie_strm_path(realistic_root, "NL | Netflix", movie(name=long_title))
    assert len(path) <= _MAX_PATH


def test_episode_strm_path_never_exceeds_the_safe_path_length_with_a_realistic_root():
    """Regression: a real provider was observed returning an episode title that
    already embeds "<Show> - S01E02 - <Real Title>" - doubled against this
    module's own prefix, the resulting filename exceeded Windows' 260-char
    MAX_PATH and crashed the whole sync with a bare "No such file or
    directory"."""
    realistic_root = "C:\\Users\\Someone\\AppData\\Roaming\\Kodi\\userdata\\addon_data\\" \
                     "plugin.video.xstreamflex\\library\\series"
    doubled_title = "Kinderen geen bezwaar 2004 (NL) - S01E02 - " * 5
    path = episode_strm_path(realistic_root, "NL | Netflix", "Kinderen geen bezwaar 2004 (NL)",
                             episode(title=doubled_title))
    assert len(path) <= _MAX_PATH
    assert path.endswith("(10).strm")


def test_episode_strm_path_keeps_the_id_suffix_intact_even_when_shrunk(tmp_path):
    root = str(tmp_path)
    path = episode_strm_path(root, "NL | Netflix", "Show Name",
                             episode(id="999999", title="X" * 300))
    assert path.endswith("(999999).strm")


# -- resilience: one bad item must not take the whole batch down -------------

def test_sync_movies_skips_a_failing_item_and_keeps_going(tmp_path, monkeypatch):
    root = str(tmp_path)
    import core.library_sync as library_sync

    real_write = library_sync._write_if_changed

    def flaky_write(path, content):
        if "bad" in path:
            raise OSError(2, "No such file or directory")
        return real_write(path, content)

    monkeypatch.setattr(library_sync, "_write_if_changed", flaky_write)

    errors = []
    written, removed = sync_movies(
        root,
        [("Src", movie(id="1", name="bad")), ("Src", movie(id="2", name="good"))],
        "plugin://plugin.video.xstreamflex/",
        on_error=lambda path, exc: errors.append((path, exc)),
    )
    # "bad" movie: both its .strm and .nfo fail (both paths contain "bad").
    assert written == 2  # good movie's .strm + .nfo
    assert len(errors) == 2
    assert all("bad" in path for path, _exc in errors)


def test_sync_episodes_skips_a_failing_item_and_keeps_going(tmp_path, monkeypatch):
    root = str(tmp_path)
    import core.library_sync as library_sync

    real_write = library_sync._write_if_changed

    def flaky_write(path, content):
        if "bad" in path:
            raise OSError(2, "No such file or directory")
        return real_write(path, content)

    monkeypatch.setattr(library_sync, "_write_if_changed", flaky_write)

    errors = []
    shows = [("Src", series(name="bad show"), [episode(id="1")]),
             ("Src", series(name="good show"), [episode(id="2")])]
    written, removed = sync_episodes(
        root, shows, "plugin://plugin.video.xstreamflex/",
        on_error=lambda path, exc: errors.append((path, exc)),
    )
    # "bad show": its tvshow.nfo and its one episode's .strm/.nfo all fail.
    assert written == 3  # good show's tvshow.nfo + its episode's .strm and .nfo
    assert len(errors) == 3


def test_sync_movies_on_error_defaults_to_a_silent_noop(tmp_path, monkeypatch):
    """No on_error passed must not raise just because nothing was supplied."""
    root = str(tmp_path)
    import core.library_sync as library_sync

    def always_fails(path, content):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(library_sync, "_write_if_changed", always_fails)
    written, removed = sync_movies(root, [("Src", movie())], "plugin://plugin.video.xstreamflex/")
    assert written == 0


# -- basic sync behaviour, now source-aware and NFO-aware ---------------------

def test_sync_movies_writes_strm_and_nfo_per_movie(tmp_path):
    root = str(tmp_path)
    written, removed = sync_movies(
        root, [("Src", movie(id="1")), ("Src", movie(id="2", name="Other"))],
        "plugin://plugin.video.xstreamflex/")
    assert written == 4  # 2 movies x (.strm + .nfo)
    assert removed == 0
    strm_files = [f for f in os.listdir(os.path.join(root, "Src")) if f.endswith(".strm")]
    nfo_files = [f for f in os.listdir(os.path.join(root, "Src")) if f.endswith(".nfo")]
    assert len(strm_files) == 2 and len(nfo_files) == 2


def test_sync_movies_is_idempotent(tmp_path):
    root = str(tmp_path)
    movies = [("Src", movie(id="1"))]
    sync_movies(root, movies, "plugin://plugin.video.xstreamflex/")

    written, removed = sync_movies(root, movies, "plugin://plugin.video.xstreamflex/")
    assert written == 0
    assert removed == 0


def test_sync_movies_removes_titles_no_longer_present(tmp_path):
    root = str(tmp_path)
    sync_movies(root, [("Src", movie(id="1")), ("Src", movie(id="2"))],
                "plugin://plugin.video.xstreamflex/")

    written, removed = sync_movies(root, [("Src", movie(id="1"))],
                                    "plugin://plugin.video.xstreamflex/")
    assert written == 0
    assert removed == 2  # dropped movie's .strm and .nfo


def test_sync_movies_rewrites_a_changed_title(tmp_path):
    root = str(tmp_path)
    sync_movies(root, [("Src", movie(id="1", name="Old Title"))],
                "plugin://plugin.video.xstreamflex/")

    written, removed = sync_movies(root, [("Src", movie(id="1", name="Old Title"))],
                                    "plugin://plugin.video.xstreamflex/")
    assert written == 0, "same content must not be rewritten (would bump mtime)"


def test_sync_movies_merges_the_same_movie_seen_under_several_categories(tmp_path):
    """A provider-side id, not the category name, is what makes a movie unique:
    one .strm/.nfo pair, filed under the first category seen, carrying every
    matching category as its own <tag> - not one duplicate pair per category
    (which is exactly what made the same film show up 20+ times for an account
    whose provider scatters titles across many overlapping VOD categories)."""
    root = str(tmp_path)
    written, removed = sync_movies(
        root, [("NL | Netflix", movie(id="1")), ("NL | Videoland", movie(id="1"))],
        "plugin://plugin.video.xstreamflex/")
    assert written == 2  # 1 movie x (.strm + .nfo), filed under the first source
    assert os.path.isdir(os.path.join(root, "NL Netflix"))
    assert not os.path.isdir(os.path.join(root, "NL Videoland"))
    with open(movie_nfo_path(root, "NL | Netflix", movie(id="1")), encoding="utf-8") as handle:
        content = handle.read()
    assert "<tag>NL | Netflix</tag>" in content
    assert "<tag>NL | Videoland</tag>" in content


def test_sync_episodes_nests_directly_by_show_no_source_folder_and_is_idempotent(tmp_path):
    root = str(tmp_path)
    shows = [("NL | Netflix", series(name="Show One"),
             [episode(id="10", season=1, episode=1), episode(id="11", season=1, episode=2)])]

    written, removed = sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")
    assert written == 5  # tvshow.nfo + 2 episodes x (.strm + .nfo)
    assert removed == 0
    assert os.path.isdir(os.path.join(root, "Show One"))
    assert os.path.isfile(os.path.join(root, "Show One", "tvshow.nfo"))

    written, removed = sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")
    assert written == 0
    assert removed == 0


def test_sync_episodes_removes_episodes_dropped_from_the_provider(tmp_path):
    root = str(tmp_path)
    shows = [("Src", series(name="Show One"), [episode(id="10"), episode(id="11")])]
    sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")

    written, removed = sync_episodes(
        root, [("Src", series(name="Show One"), [episode(id="10")])],
        "plugin://plugin.video.xstreamflex/")
    assert removed == 2  # the dropped episode's .strm and its .nfo


def test_movie_strm_path_includes_the_id_to_avoid_collisions(tmp_path):
    root = str(tmp_path)
    path1 = movie_strm_path(root, "Src", movie(id="1", name="Same Title"))
    path2 = movie_strm_path(root, "Src", movie(id="2", name="Same Title"))
    assert path1 != path2


def test_matches_country_ignores_punctuation_after_the_prefix():
    assert matches_country("NL | Filmclub 2026", "NL")
    assert matches_country("nl-Netflix", "NL")
    assert not matches_country("BE | Cinemax", "NL")
    assert not matches_country("NLD | Not Actually NL", "NL")


def test_sync_state_round_trips(tmp_path):
    state_dir = str(tmp_path)
    assert last_sync_time(state_dir, "p1") == 0
    assert is_sync_stale(state_dir, "p1", 3600)

    write_sync_state(state_dir, "p1", movies_written=5, movies_removed=1,
                      series_written=2, series_removed=0)

    assert last_sync_time(state_dir, "p1") > 0
    assert not is_sync_stale(state_dir, "p1", 3600)
    assert is_sync_stale(state_dir, "p1", 0)


def test_sync_state_is_per_provider(tmp_path):
    state_dir = str(tmp_path)
    write_sync_state(state_dir, "p1", movies_written=1, movies_removed=0,
                      series_written=0, series_removed=0)
    assert last_sync_time(state_dir, "p2") == 0


def test_unencodable_path_skips_one_item_not_the_whole_sync(tmp_path, monkeypatch):
    """Steam exports LC_ALL=C to everything it launches, so Kodi started from a
    Steam shortcut has an ASCII filesystem encoding and every accented title
    raises UnicodeEncodeError. That must skip the title, not abort the sync."""
    import core.library_sync as ls

    real_write = ls._write_if_changed
    calls = []

    def flaky(path, content):
        if "Amelie" in path:
            raise UnicodeEncodeError("ascii", "é", 0, 1, "ordinal not in range(128)")
        return real_write(path, content)

    monkeypatch.setattr(ls, "_write_if_changed", flaky)

    root = str(tmp_path / "movies")
    movies = [
        (("NL | ACTIE", movie(id="1", name="Amelie")),),
        (("NL | ACTIE", movie(id="2", name="Plain Title")),),
    ]
    flat = [pair for group in movies for pair in group]

    written, _ = ls.sync_movies(root, flat, "http://base",
                                on_error=lambda p, e: calls.append(p))

    assert written > 0, "the encodable title must still be written"
    assert calls, "the failing path must be reported through on_error"
    assert all("Amelie" in path for path in calls)


def test_sync_episodes_writes_an_nfo_beside_every_episode(tmp_path):
    """Without a per-episode NFO Kodi adds the show and none of its episodes.

    Its scanner only calls AddVideo() straight away when it finds an episode
    NFO; otherwise it needs an episode guide, which neither an online scraper
    (no <episodeguide> in our tvshow.nfo) nor the local scraper (excluded from
    guide fetching) can supply, and it skips the episode entirely. This was
    live: 2391 shows in the library, 0 episodes.
    """
    root = str(tmp_path)
    shows = [("NL | Netflix", series(name="Show One"),
              [episode(id="10", season=2, episode=7, title="Ep Title")])]
    sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")

    strm = [p for p in os.listdir(os.path.join(root, "Show One"))
            if p.endswith(".strm")][0]
    nfo_path = os.path.join(root, "Show One", strm[: -len(".strm")] + ".nfo")
    assert os.path.isfile(nfo_path), "episode .nfo must sit beside its .strm"

    content = open(nfo_path, encoding="utf-8").read()
    assert "<episodedetails>" in content
    assert "<season>2</season>" in content
    assert "<episode>7</episode>" in content
    assert "<title>Ep Title</title>" in content


def test_write_sync_state_is_atomic_and_survives_a_concurrent_writer(tmp_path):
    """A torn state file reads back as "never synced", which triggers a full
    sync on every single startup - the bug that made this feel broken for
    days. The write must be all-or-nothing.
    """
    from core.library_sync import last_sync_time, write_sync_state

    state_dir = str(tmp_path)
    for i in range(20):
        write_sync_state(state_dir, "prov", movies_written=i, movies_removed=0,
                         series_written=i, series_removed=0)
        assert last_sync_time(state_dir, "prov") > 0, "state must always parse"

    # No temp files left behind.
    assert not [p for p in os.listdir(state_dir) if p.endswith(".tmp")]


def test_sync_lock_refuses_a_second_holder(tmp_path):
    """Two concurrent syncs did the same 110k writes twice and raced on the
    state file - observed live, both finishing in the same second."""
    from core.library_sync import sync_lock

    state_dir = str(tmp_path)
    with sync_lock(state_dir) as first:
        assert first is True
        with sync_lock(state_dir) as second:
            assert second is False, "a second sync must not start"

    # Released again afterwards.
    with sync_lock(state_dir) as third:
        assert third is True


def test_sync_request_is_consumed_exactly_once(tmp_path):
    """Removed before the sync runs, not after: a request that crashes the
    sync must not be retried forever."""
    from core.library_sync import request_sync, take_sync_request

    state_dir = str(tmp_path)
    assert take_sync_request(state_dir) is False
    request_sync(state_dir)
    assert take_sync_request(state_dir) is True
    assert take_sync_request(state_dir) is False


REAL_VOD_INFO = {
    # Shape confirmed against a real account's get_vod_info response.
    "plot": "", "description": "Een korte beschrijving.",
    "releasedate": "2026-04-24", "genre": "Drama, Romance",
    "director": "Rodante Pajemna Jr.",
    "cast": "Apphle Celso, Van Allen Ong",
    "duration_secs": 4544, "rating": "3.8", "tmdb_id": "1669051",
    "country": "Philippines",
    "backdrop_path": ["https://image.tmdb.org/t/p/w1280/a2.jpg"],
    "movie_image": "https://image.tmdb.org/t/p/w600/8z.jpg",
}


def test_movie_nfo_carries_premiered_so_kodi_can_sort_by_release_date():
    """Kodi derives its "Release date" sort order from <premiered>. With only
    <year> - which is all the VOD *list* endpoint offers, and often not even
    that - the only meaningful ordering left is by title, and "date added"
    means when we imported it, not when the film came out."""
    from core.library_sync import movie_nfo_content

    nfo = movie_nfo_content(movie(id="1"), ["Src"], None, REAL_VOD_INFO)

    assert "<premiered>2026-04-24</premiered>" in nfo
    assert "<year>2026</year>" in nfo, "year falls back out of the release date"


def test_movie_nfo_splits_comma_separated_people_and_genres():
    """The provider comma-separates these in one string; Kodi wants one
    element each, or it renders "Drama, Romance" as a single genre."""
    from core.library_sync import movie_nfo_content

    nfo = movie_nfo_content(movie(id="1"), ["Src"], None, REAL_VOD_INFO)

    assert "<genre>Drama</genre>" in nfo and "<genre>Romance</genre>" in nfo
    assert "<name>Apphle Celso</name>" in nfo and "<name>Van Allen Ong</name>" in nfo
    assert "<runtime>76</runtime>" in nfo, "duration_secs is seconds, runtime is minutes"
    assert '<uniqueid type="tmdb">1669051</uniqueid>' in nfo


def test_movie_nfo_stays_valid_without_details():
    """A detail call that failed must thin the NFO, not break it - one
    unreachable movie cannot be allowed to cost the whole catalogue."""
    from core.library_sync import movie_nfo_content

    nfo = movie_nfo_content(movie(id="1", name="Some Film"), ["Src"], None, None)

    assert "<title>Some Film</title>" in nfo
    assert "<premiered>" not in nfo and "<actor>" not in nfo
    assert nfo.strip().endswith("</movie>")


def test_collect_movie_details_skips_a_failing_movie(monkeypatch):
    """One movie the provider chokes on must not abort the sweep."""
    from core.library_sync import collect_movie_details

    class Provider:
        def movie_info(self, movie_id):
            if movie_id == "bad":
                raise RuntimeError("boom")
            return {"info": {"releasedate": "2020-01-02"}}

    pairs = [("Src", movie(id="bad")), ("Src", movie(id="good"))]
    details = collect_movie_details(Provider(), pairs)

    assert details["good"]["releasedate"] == "2020-01-02"
    assert details["bad"] == {}


def test_collect_movie_details_asks_once_per_movie_not_per_category():
    """A film listed under six overlapping categories is still one film."""
    from core.library_sync import collect_movie_details

    calls = []

    class Provider:
        def movie_info(self, movie_id):
            calls.append(movie_id)
            return {"info": {}}

    pairs = [("A", movie(id="7")), ("B", movie(id="7")), ("C", movie(id="7"))]
    collect_movie_details(Provider(), pairs)

    assert calls == ["7"]


# -- clean_title: what goes in <title>, and what must survive untouched --------
#
# Every example below is taken from the live 7.8k-title catalogue unless it is
# marked as a guard case. The cost of being wrong is asymmetric: a title that
# keeps a stray "4K" is mildly ugly, a title that lost a real word is a film
# the user can no longer find by name.


def test_clean_title_strips_the_trailing_country_marker():
    assert clean_title("USS Christmas (NL)") == "USS Christmas"
    assert clean_title("Kesong Puti (NL)") == "Kesong Puti"
    assert clean_title("Sex Education (ENG)") == "Sex Education"
    assert clean_title("Ann Droid (NL)") == "Ann Droid"


def test_clean_title_strips_the_less_common_markers_too():
    """All of these appear as trailing bracket groups in the live catalogue."""
    assert clean_title("Culprits (NL AUDIO)") == "Culprits"
    assert clean_title("Iets (NL GESPROKEN)") == "Iets"
    assert clean_title("Iets (NLGESPROKEN)") == "Iets"
    assert clean_title("Iets (NL-BE)") == "Iets"
    assert clean_title("Iets (MULTI)") == "Iets"
    assert clean_title("Iets (BE)") == "Iets"


def test_clean_title_strips_a_resolution_marker():
    assert clean_title("Aquaman and the Lost Kingdom 4K (NL)") == \
        "Aquaman and the Lost Kingdom"
    assert clean_title("Avengers: Age of Ultron 4K (NL)") == "Avengers: Age of Ultron"
    assert clean_title("Some Film 1080p (NL)") == "Some Film"
    assert clean_title("Some Film UHD (NL)") == "Some Film"


def test_clean_title_unwinds_a_whole_run_of_decorations():
    """The provider mixes separators and order freely; stripping repeats from
    the tail until nothing more comes off, which is what makes one rule cover
    all four of these shapes."""
    assert clean_title("Ballerina 4K 2025 (NL)") == "Ballerina"
    assert clean_title("Karate Kid: Legends  - 4K - 2025 (NL)") == "Karate Kid: Legends"
    assert clean_title("The Old Guard 2 - 4K - 2025 (NL)") == "The Old Guard 2"
    assert clean_title("The Avengers - 4K - (NL)") == "The Avengers"
    assert clean_title("Thunderbolts* - 4K (NL)") == "Thunderbolts*"


def test_clean_title_strips_a_trailing_year_in_either_form():
    """<year>/<premiered> already carry this, and it sorts badly in Kodi."""
    assert clean_title("Megan Is Missing 2011 (NL)") == "Megan Is Missing"
    assert clean_title("Aftermath - 2024 (NL)") == "Aftermath"
    assert clean_title("Ook dat nog! (2025) (NL)") == "Ook dat nog!"


def test_clean_title_keeps_a_year_that_is_part_of_the_title():
    """"Blade Runner 2049" is the whole reason the year rule has an upper
    bound: it sits exactly where a provider-appended year sits, and only the
    fact that no film released today can carry it tells the two apart."""
    assert clean_title("Blade Runner 2049 (NL)") == "Blade Runner 2049"
    assert clean_title("2073 (NL)") == "2073"
    assert clean_title("Dracula 3000 (NL)") == "Dracula 3000"


def test_clean_title_keeps_a_year_that_is_not_at_the_end():
    assert clean_title("2001: A Space Odyssey (NL)") == "2001: A Space Odyssey"
    assert clean_title("Moto3 2026 Great Britain (ENG)") == "Moto3 2026 Great Britain"


def test_clean_title_keeps_a_title_that_is_only_a_year():
    """Stripping would leave nothing at all, so it does not."""
    assert clean_title("1922 (NL)") == "1922"
    assert clean_title("2010 (NL)") == "2010"


def test_clean_title_keeps_a_year_range():
    """A second four-digit number in front of it means a span, not a stream
    label - and the bracketed form must not be half-eaten into "(1972"."""
    assert clean_title("Atatürk 1881 - 1919 (NL)") == "Atatürk 1881 - 1919"
    assert clean_title("1992 - 2024 (NL)") == "1992 - 2024"
    assert clean_title("Glen Campbell | Live Anthology (1972-2001) (NL)") == \
        "Glen Campbell | Live Anthology (1972-2001)"


def test_clean_title_keeps_a_year_that_is_the_end_of_a_date():
    assert clean_title("AEW Rampage - 26.01.2024 (NL)") == "AEW Rampage - 26.01.2024"


def test_clean_title_keeps_3d_because_it_is_part_of_real_titles():
    """"Jackass 3D" and "Saw 3D" are the released titles; "Saw 4K" is not.
    Both shapes are in the same catalogue, which is why 3D is not in the
    resolution vocabulary."""
    assert clean_title("Jackass 3D (NL)") == "Jackass 3D"
    assert clean_title("Saw 3D (NL)") == "Saw 3D"
    assert clean_title("Saw 4K (NL)") == "Saw"


def test_clean_title_keeps_a_title_that_is_nothing_but_a_marker():
    """A film genuinely called "4K" keeps its name rather than losing it."""
    assert clean_title("4K") == "4K"
    assert clean_title("HD") == "HD"


def test_clean_title_keeps_punctuation_heavy_real_titles():
    assert clean_title("'Allo 'Allo! (NL)") == "'Allo 'Allo!"
    assert clean_title("#BringBackAlice (NL)") == "#BringBackAlice"
    assert clean_title("Se7en (NL)") == "Se7en"
    assert clean_title("11.22.63 (NL)") == "11.22.63"
    assert clean_title("*batteries not included (NL)") == "*batteries not included"
    assert clean_title("$POSITIONS (NL)") == "$POSITIONS"


def test_clean_title_keeps_a_mixed_case_bracket_group():
    """Only an all-caps code reads as a stream marker. Everything else in a
    trailing bracket in the live catalogue was part of the title."""
    assert clean_title("Billie Eilish: Live at the O2 (Extended Cut) (NL)") == \
        "Billie Eilish: Live at the O2 (Extended Cut)"
    assert clean_title("Snöänglar (Sneeuwengelen) (NL)") == \
        "Snöänglar (Sneeuwengelen)"
    assert clean_title("Bruce Springsteen: In Concert/MTV (Un)Plugged (NL)") == \
        "Bruce Springsteen: In Concert/MTV (Un)Plugged"


def test_clean_title_keeps_us_and_uk_because_they_disambiguate_a_remake():
    """"The Office (US)" is a different show from "The Office"; "(NL)" is the
    same film with a Dutch stream. The provider's own markers here are
    NL/ENG/BE, so the two never collide in practice."""
    assert clean_title("The Office (US)") == "The Office (US)"
    assert clean_title("Ghosts (UK)") == "Ghosts (UK)"
    assert clean_title("The Office US 2005 (NL)") == "The Office US"
    assert clean_title("Shameless USA (NL)") == "Shameless USA"


def test_clean_title_survives_degenerate_input():
    assert clean_title("") == ""
    assert clean_title("   ") == ""
    assert clean_title(None) == ""
    assert clean_title("  Spaced   Out  (NL) ") == "Spaced Out"


def test_clean_title_does_not_reach_into_a_path(tmp_path):
    """The whole point of confining this to the NFO: an existing library is
    six figures of files whose names came from the raw provider title. If
    cleaning ever leaked into a path, every one of them would be rewritten and
    rescanned for a cosmetic gain."""
    root = str(tmp_path)
    m = movie(id="1379605", name="Ballerina 4K 2025 (NL)")

    assert movie_strm_path(root, "Src", m).endswith(
        os.path.join("Src", "Ballerina 4K 2025 (NL) (1379605).strm"))
    assert show_nfo_path(root, "Ann Droid (NL)") == os.path.join(
        root, "Ann Droid (NL)", "tvshow.nfo")


def test_clean_title_does_not_cost_filename_uniqueness(tmp_path):
    """Uniqueness rides on the provider id in the filename, not on the
    decorations - two movies that clean to the same title still get their own
    files."""
    root = str(tmp_path)
    a = movie(id="1", name="Culprits (NL)")
    b = movie(id="2", name="Culprits (ENG)")

    assert movie_strm_path(root, "Src", a) != movie_strm_path(root, "Src", b)


def test_movie_nfo_title_is_cleaned_but_the_strm_url_is_not():
    """The play URL carries the provider's own name through to playback; only
    what Kodi displays is cleaned."""
    m = movie(id="7", name="Aquaman and the Lost Kingdom 4K (NL)")

    assert "<title>Aquaman and the Lost Kingdom</title>" in movie_nfo_content(m, "Src")
    assert "4K" in movie_strm_content("http://plugin", m)


def test_show_nfo_title_is_cleaned():
    content = show_nfo_content(series(name="'Allo 'Allo! (NL)"), "NL | Netflix")
    assert "<title>'Allo 'Allo!</title>" in content
