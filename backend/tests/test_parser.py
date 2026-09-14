from pathlib import Path

import pytest

from app.services.parser import (
    _clean_episode_title,
    detect_season_dirs,
    folder_has_provider_tag,
    folder_looks_like_movie,
    folder_root_videos,
    is_video,
    list_season_episodes,
    parse_episode_filename,
    parse_folder_name,
    parse_movie_filename,
    season_number_from_dir,
)


# ---- parse_folder_name ------------------------------------------------------

@pytest.mark.parametrize("name,title,year,provider,eid", [
    ("Show (2020) {tvdb-12345}", "Show", 2020, "tvdb", "12345"),
    ("Movie (1999) {tmdb-42}", "Movie", 1999, "tmdb", "42"),
    ("Movie {imdb-tt0111161}", "Movie", None, "imdb", "tt0111161"),
    ("Show (2020)", "Show", 2020, None, None),
    ("Just A Show", "Just A Show", None, None, None),
    ("Movie (2020) {edition-Director's Cut} {tmdb-5}", "Movie", 2020, "tmdb", "5"),
])
def test_parse_folder_name(name, title, year, provider, eid):
    pf = parse_folder_name(name)
    assert (pf.title, pf.year, pf.provider, pf.external_id) == (title, year, provider, eid)
    assert pf.raw == name.strip()


def test_folder_has_provider_tag():
    assert folder_has_provider_tag("Show (2020) {tvdb-9}") == ("tvdb", "9")
    assert folder_has_provider_tag("Show (2020)") == (None, None)


# ---- parse_episode_filename -------------------------------------------------

def test_non_video_returns_none():
    assert parse_episode_filename(Path("Show - S01E01.srt")) is None
    assert parse_episode_filename(Path("Show - S01E01.nfo")) is None


def test_standard_sxxexx():
    ep = parse_episode_filename(
        Path("Show (2020) - S01E02 - The Pilot [WEBDL-1080p][x264]-GROUP.mkv"))
    assert ep.parsed
    assert (ep.season, ep.episode, ep.end_episode) == (1, 2, None)
    assert ep.raw_title == "The Pilot"
    assert ep.extension == ".mkv"


def test_multi_episode():
    ep = parse_episode_filename(Path("Show - S01E02E03 - Two Parter.mkv"))
    assert (ep.season, ep.episode, ep.end_episode) == (1, 2, 3)
    ep = parse_episode_filename(Path("Show - S01E02-03.mkv"))
    assert ep.end_episode == 3


def test_multi_episode_prefixed_range_is_captured():
    # Sonarr's prefixed range keeps both episode numbers for NFO generation.
    ep = parse_episode_filename(Path("Show - S01E02-E03.mkv"))
    assert (ep.episode, ep.end_episode) == (2, 3)


def test_sxxexx_case_insensitive():
    ep = parse_episode_filename(Path("show - s02e05.mp4"))
    assert ep.parsed
    assert (ep.season, ep.episode) == (2, 5)


def test_anime_number_season_defaults_to_1():
    ep = parse_episode_filename(Path("[SubsPlease] Show - 05v2 [1080p][ABC].mkv"))
    assert ep.parsed
    assert (ep.season, ep.episode) == (1, 5)


@pytest.mark.parametrize("n", [0, 2000])
def test_anime_guard_rejects_out_of_range(n):
    ep = parse_episode_filename(Path(f"[Grp] Show - {n} [x].mkv"))
    assert not ep.parsed
    assert (ep.season, ep.episode) == (0, 0)


def test_anime_guard_accepts_1999():
    ep = parse_episode_filename(Path("[Grp] Show - 1999 [x].mkv"))
    assert ep.parsed
    assert ep.episode == 1999


def test_anime_requires_leading_group_bracket():
    # "Movie - 1" without [Group] must not parse as an anime episode
    ep = parse_episode_filename(Path("Movie - 1.mkv"))
    assert not ep.parsed


def test_daily_format():
    ep = parse_episode_filename(Path("Show (2020) - 2023-05-17 - Some Day.mkv"))
    assert ep.parsed
    assert ep.air_date == "2023-05-17"
    assert (ep.season, ep.episode) == (0, 0)
    assert ep.raw_title == "Some Day"


def test_daily_invalid_date_falls_through_to_unparsed():
    ep = parse_episode_filename(Path("Show - 2023-13-45 - Bad.mkv"))
    assert not ep.parsed
    assert ep.air_date is None


def test_sxxexx_wins_over_daily():
    ep = parse_episode_filename(Path("Show - S03E01 - 2023-05-17.mkv"))
    assert (ep.season, ep.episode) == (3, 1)
    assert ep.air_date is None


def test_unparseable_video_kept_with_parsed_false():
    ep = parse_episode_filename(Path("randomfile.mkv"))
    assert not ep.parsed
    assert (ep.season, ep.episode) == (0, 0)
    assert ep.extension == ".mkv"


# ---- _clean_episode_title ---------------------------------------------------

@pytest.mark.parametrize("rest,expected", [
    (" - The Pilot [WEBDL-1080p][x264]-GROUP", "The Pilot"),
    (" - Some.Title-GRP", "Some Title"),
    (" - [1080p]", None),
    ("", None),
])
def test_clean_episode_title(rest, expected):
    assert _clean_episode_title(rest) == expected


def test_clean_episode_title_truncates_hyphenated_titles():
    # NOTE: pins current behavior — the -GROUP heuristic eats the tail of a
    # legit hyphenated title. Known bug, tracked separately.
    assert _clean_episode_title(" - Spider-Man") == "Spider"


# ---- parse_movie_filename ---------------------------------------------------

def test_parse_movie_filename_full():
    pm = parse_movie_filename(Path("Movie (2020) {tmdb-123} [Bluray-1080p]-GRP.mkv"))
    assert pm.title == "Movie"
    assert pm.year == 2020
    assert (pm.provider, pm.external_id) == ("tmdb", "123")


def test_parse_movie_filename_plain():
    pm = parse_movie_filename(Path("Movie (2020).mkv"))
    assert (pm.title, pm.year, pm.provider) == ("Movie", 2020, None)


def test_parse_movie_filename_edition_tag_stripped():
    pm = parse_movie_filename(Path("Movie (2020) {edition-Director's Cut} {tmdb-9}.mkv"))
    assert (pm.title, pm.year, pm.external_id) == ("Movie", 2020, "9")


def test_parse_movie_filename_fallback_title():
    pm = parse_movie_filename(Path("justafile.mkv"))
    assert pm.title == "justafile"
    assert pm.year is None


# ---- misc pure helpers ------------------------------------------------------

def test_is_video():
    assert is_video(Path("a.MKV"))
    assert is_video(Path("a.mp4"))
    assert not is_video(Path("a.srt"))


@pytest.mark.parametrize("name,expected", [
    ("Season 01", 1),
    ("Season 2", 2),
    ("season 10", 10),
    ("Specials", 0),
    ("extras", 0),
    ("Whatever", 0),
])
def test_season_number_from_dir(name, expected):
    assert season_number_from_dir(name) == expected


# ---- filesystem helpers -----------------------------------------------------

def test_detect_season_dirs(tmp_path, touch):
    touch("S/Season 01/e.mkv")
    touch("S/Season 02/e.mkv")
    touch("S/Specials/e.mkv")
    touch("S/extras/e.mkv")
    touch("S/Behind The Scenes/e.mkv")
    names = {d.name for d in detect_season_dirs(tmp_path / "S")}
    # set compare: sorted(iterdir()) order is case-insensitive on Windows only
    assert names == {"Season 01", "Season 02", "Specials", "extras"}
    assert detect_season_dirs(tmp_path / "missing") == []


def test_list_season_episodes(tmp_path, touch):
    touch("S/Season 01/Show - S01E01.mkv")
    touch("S/Season 01/Show - S01E02.mkv")
    touch("S/Season 01/Show - S01E01.nfo")
    touch("S/Season 01/unparseable.mkv")
    eps = list_season_episodes(tmp_path / "S/Season 01")
    assert len(eps) == 3  # unparseable video kept (parsed=False)
    assert sum(1 for e in eps if e.parsed) == 2
    assert list_season_episodes(tmp_path / "missing") == []


def test_folder_root_videos(tmp_path, touch):
    touch("M/a.mkv")
    touch("M/b.srt")
    touch("M/Season 01/c.mkv")  # not recursive
    vids = folder_root_videos(tmp_path / "M")
    assert [v.name for v in vids] == ["a.mkv"]


def test_folder_looks_like_movie(tmp_path, touch):
    touch("Movie (2020)/Movie (2020).mkv")
    assert folder_looks_like_movie(tmp_path / "Movie (2020)")

    touch("SeriesRoot/Show - S01E01.mkv")
    assert not folder_looks_like_movie(tmp_path / "SeriesRoot")

    touch("WithSeasons/Season 01/e.mkv")
    assert not folder_looks_like_movie(tmp_path / "WithSeasons")

    (tmp_path / "NoVideos").mkdir()
    assert not folder_looks_like_movie(tmp_path / "NoVideos")
