import os

from core.library_sync import (
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


def test_episode_strm_path_nests_under_a_show_folder():
    path = episode_strm_path("/lib", "Turner & Hooch", episode())
    assert path == os.path.join("/lib", "Turner & Hooch",
                                 "Turner & Hooch - S01E02 - The Pilot (10).strm")


def test_episode_strm_path_strips_illegal_characters_from_the_show_name():
    path = episode_strm_path("/lib", "Show: Part Two", episode())
    assert os.path.dirname(path) == os.path.join("/lib", "Show Part Two")


def test_sync_movies_writes_one_file_per_movie(tmp_path):
    root = str(tmp_path)
    written, removed = sync_movies(root, [movie(id="1"), movie(id="2", name="Other")],
                                    "plugin://plugin.video.xstreamflex/")
    assert written == 2
    assert removed == 0
    assert len([f for f in os.listdir(root) if f.endswith(".strm")]) == 2


def test_sync_movies_is_idempotent(tmp_path):
    root = str(tmp_path)
    movies = [movie(id="1")]
    sync_movies(root, movies, "plugin://plugin.video.xstreamflex/")

    written, removed = sync_movies(root, movies, "plugin://plugin.video.xstreamflex/")
    assert written == 0
    assert removed == 0


def test_sync_movies_removes_titles_no_longer_present(tmp_path):
    root = str(tmp_path)
    sync_movies(root, [movie(id="1"), movie(id="2")], "plugin://plugin.video.xstreamflex/")

    written, removed = sync_movies(root, [movie(id="1")], "plugin://plugin.video.xstreamflex/")
    assert written == 0
    assert removed == 1
    assert len([f for f in os.listdir(root) if f.endswith(".strm")]) == 1


def test_sync_movies_rewrites_a_changed_title(tmp_path):
    root = str(tmp_path)
    sync_movies(root, [movie(id="1", name="Old Title")], "plugin://plugin.video.xstreamflex/")

    written, removed = sync_movies(root, [movie(id="1", name="Old Title")],
                                    "plugin://plugin.video.xstreamflex/")
    assert written == 0, "same content must not be rewritten (would bump mtime)"


def test_sync_episodes_nests_by_show_and_is_idempotent(tmp_path):
    root = str(tmp_path)
    shows = [("Show One", [episode(id="10", season=1, episode=1),
                            episode(id="11", season=1, episode=2)])]

    written, removed = sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")
    assert written == 2
    assert removed == 0
    assert os.path.isdir(os.path.join(root, "Show One"))

    written, removed = sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")
    assert written == 0
    assert removed == 0


def test_sync_episodes_removes_episodes_dropped_from_the_provider(tmp_path):
    root = str(tmp_path)
    shows = [("Show One", [episode(id="10"), episode(id="11")])]
    sync_episodes(root, shows, "plugin://plugin.video.xstreamflex/")

    written, removed = sync_episodes(
        root, [("Show One", [episode(id="10")])], "plugin://plugin.video.xstreamflex/")
    assert removed == 1


def test_movie_strm_path_includes_the_id_to_avoid_collisions():
    path1 = movie_strm_path("/lib", movie(id="1", name="Same Title"))
    path2 = movie_strm_path("/lib", movie(id="2", name="Same Title"))
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
