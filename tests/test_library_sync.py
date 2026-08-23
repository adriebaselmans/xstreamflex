import os

from core.library_sync import (
    _MAX_PATH,
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
    assert "<title>Some Movie (NL)</title>" in content
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
