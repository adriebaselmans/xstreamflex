import os

from core.library_sync import (
    _MAX_PATH,
    episode_strm_content,
    episode_strm_path,
    is_sync_stale,
    last_sync_time,
    matches_country,
    movie_strm_content,
    movie_strm_path,
    safe_filename,
    sync_episodes,
    sync_movies,
    write_sync_state,
)
from core.models import Episode, Movie


def movie(**kwargs):
    base = dict(id="1", name="Some Movie (NL)", container_extension="mkv")
    base.update(kwargs)
    return Movie(**base)


def episode(**kwargs):
    base = dict(id="10", series_id="1", season=1, episode=2, title="The Pilot",
                container_extension="mkv")
    base.update(kwargs)
    return Episode(**base)


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


def test_episode_strm_path_nests_under_source_then_show(tmp_path):
    """The show name is deliberately not repeated in the filename - Kodi's TV
    scanner already gets it from the parent folder, and repeating it was the
    main contributor to real filenames exceeding Windows' MAX_PATH."""
    root = str(tmp_path)
    path = episode_strm_path(root, "NL | Netflix", "Turner & Hooch", episode())
    assert path == os.path.join(root, "NL Netflix", "Turner & Hooch",
                                "S01E02 - The Pilot (10).strm")


def test_episode_strm_path_strips_illegal_characters_from_the_show_name(tmp_path):
    root = str(tmp_path)
    path = episode_strm_path(root, "NL | Netflix", "Show: Part Two", episode())
    assert os.path.basename(os.path.dirname(path)) == "Show Part Two"


def test_movie_strm_path_nests_under_source(tmp_path):
    root = str(tmp_path)
    path = movie_strm_path(root, "NL | Videoland", movie())
    assert path == os.path.join(root, "NL Videoland", "Some Movie (NL) (1).strm")


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
    calls = []

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
    assert written == 1
    assert len(errors) == 1
    assert "bad" in errors[0][0]


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
    shows = [("Src", "bad show", [episode(id="1")]), ("Src", "good show", [episode(id="2")])]
    written, removed = sync_episodes(
        root, shows, "plugin://plugin.video.xstreamflex/",
        on_error=lambda path, exc: errors.append((path, exc)),
    )
    assert written == 1
    assert len(errors) == 1


def test_sync_movies_on_error_defaults_to_a_silent_noop(tmp_path, monkeypatch):
    """No on_error passed must not raise just because nothing was supplied."""
    root = str(tmp_path)
    import core.library_sync as library_sync

    def always_fails(path, content):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(library_sync, "_write_if_changed", always_fails)
    written, removed = sync_movies(root, [("Src", movie())], "plugin://plugin.video.xstreamflex/")
    assert written == 0


# -- basic sync behaviour, now source-aware -----------------------------------

def test_sync_movies_writes_one_file_per_movie(tmp_path):
    root = str(tmp_path)
    written, removed = sync_movies(
        root, [("Src", movie(id="1")), ("Src", movie(id="2", name="Other"))],
        "plugin://plugin.video.xstreamflex/")
    assert written == 2
    assert removed == 0


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
    assert removed == 1


def test_sync_movies_rewrites_a_changed_title(tmp_path):
    root = str(tmp_path)
    sync_movies(root, [("Src", movie(id="1", name="Old Title"))],
                "plugin://plugin.video.xstreamflex/")

    written, removed = sync_movies(root, [("Src", movie(id="1", name="Old Title"))],
                                    "plugin://plugin.video.xstreamflex/")
    assert written == 0, "same content must not be rewritten (would bump mtime)"


def test_sync_movies_puts_the_same_title_under_each_source_it_appears_in(tmp_path):
    """Matches the provider's own structure rather than silently deduplicating:
    a movie listed under two source apps is available from both."""
    root = str(tmp_path)
    written, removed = sync_movies(
        root, [("NL | Netflix", movie(id="1")), ("NL | Videoland", movie(id="1"))],
        "plugin://plugin.video.xstreamflex/")
    assert written == 2
    assert os.path.isdir(os.path.join(root, "NL Netflix"))
    assert os.path.isdir(os.path.join(root, "NL Videoland"))


def test_sync_episodes_nests_by_source_then_show_and_is_idempotent(tmp_path):
    root = str(tmp_path)
    shows = [("NL | Netflix", "Show One", [episode(id="10", season=1, episode=1),
                                           episode(id="11", season=1, episode=2)])]

    written, removed = sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")
    assert written == 2
    assert removed == 0
    assert os.path.isdir(os.path.join(root, "NL Netflix", "Show One"))

    written, removed = sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")
    assert written == 0
    assert removed == 0


def test_sync_episodes_removes_episodes_dropped_from_the_provider(tmp_path):
    root = str(tmp_path)
    shows = [("Src", "Show One", [episode(id="10"), episode(id="11")])]
    sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")

    written, removed = sync_episodes(
        root, [("Src", "Show One", [episode(id="10")])], "plugin://plugin.video.xstreamflex/")
    assert removed == 1


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
