import pytest

from app.services.renamer import (
    build_context,
    clean_title,
    render_sonarr_template,
    sanitize,
)


CTX = {
    "title": "Show",
    "series_titleyear": "Show (2020)",
    "season": 1,
    "episode": 2,
    "episode_title": "The Pilot",
    "episode_cleantitle": "The Pilot",
    "quality_full": "WEBDL-1080p",
    "video_codec": "x265",
    "video_bit_depth": "10",
    "audio_codec": "AAC",
    "audio_channels": "2.0",
    "release_group": "GRP",
    "tvdb_id": "12345",
}


# ---- plain tokens + padding -------------------------------------------------

def test_plain_tokens_and_padding():
    out = render_sonarr_template(
        "{Series Title} - S{season:00}E{episode:00} - {Episode CleanTitle}", CTX)
    assert out == "Show - S01E02 - The Pilot"


def test_padding_widths():
    assert render_sonarr_template("{episode:000}", CTX) == "002"
    assert render_sonarr_template("{episode}", CTX) == "2"


def test_unknown_token_resolves_empty():
    assert render_sonarr_template("A {Bogus Token}", CTX) == "A"


def test_direct_ctx_key_fallback():
    # unaliased tokens fall back to lowercased space->underscore ctx keys
    assert render_sonarr_template("{video codec}", CTX) == "x265"


# ---- conditional groups {[...]} ---------------------------------------------

def test_cond_group_renders_in_brackets():
    assert render_sonarr_template("{[Quality Full]}", CTX) == "[WEBDL-1080p]"


def test_cond_group_drops_when_empty():
    ctx = dict(CTX, quality_full="")
    assert render_sonarr_template("Title {[Quality Full]}", ctx) == "Title"


def test_cond_group_multi_token():
    tpl = "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
    assert render_sonarr_template(tpl, CTX) == "[AAC 2.0]"
    assert render_sonarr_template(tpl, dict(CTX, audio_channels="")) == "[AAC]"
    assert render_sonarr_template(
        tpl, dict(CTX, audio_codec="", audio_channels="")) == ""


# ---- square-bracket groups [{Token}suffix] ----------------------------------

def test_bracket_group_with_suffix():
    assert render_sonarr_template("[{MediaInfo VideoBitDepth}bit]", CTX) == "[10bit]"


def test_bracket_group_drops_when_empty():
    ctx = dict(CTX, video_bit_depth="")
    assert render_sonarr_template("Title [{MediaInfo VideoBitDepth}bit]", ctx) == "Title"


# ---- prefix-conditional {-Token} --------------------------------------------

def test_prefix_conditional():
    assert render_sonarr_template("Name {-Release Group}", CTX) == "Name -GRP"
    assert render_sonarr_template(
        "Name {-Release Group}", dict(CTX, release_group="")) == "Name"


# ---- nested groups ----------------------------------------------------------

def test_nested_group():
    assert render_sonarr_template("{tvdb-{TvdbId}}", CTX) == "tvdb-12345"
    assert render_sonarr_template("{tvdb-{TvdbId}}", dict(CTX, tvdb_id="")) == ""


# ---- whitespace cleanup -----------------------------------------------------

def test_cleanup_space_before_extension():
    ctx = dict(CTX, quality_full="")
    out = render_sonarr_template("Name {[Quality Full]}.mkv", ctx)
    assert out == "Name.mkv"


def test_cleanup_trailing_dash_before_extension():
    ctx = dict(CTX, release_group="")
    out = render_sonarr_template("Name -{Release Group}.mkv", ctx)
    assert out == "Name.mkv"


def test_full_sonarr_style_template():
    tpl = ("{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} "
           "{[Quality Full]} {-Release Group}")
    assert render_sonarr_template(tpl, CTX) == \
        "Show (2020) - S01E02 - The Pilot [WEBDL-1080p] -GRP"


# ---- sanitize / clean_title -------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ('A<B>:C"D', "A B C D"),
    ("AC/DC", "AC DC"),
    ("Name...", "Name"),
    ("  spaced   out  ", "spaced out"),
    ("", ""),
])
def test_sanitize(raw, expected):
    assert sanitize(raw) == expected


def test_clean_title_is_sanitize():
    assert clean_title("What / If?") == sanitize("What / If?")


# ---- _lookup alias case bug (pinned) ----------------------------------------

def test_series_titlethe_alias_unreachable():
    # NOTE: pins current behavior — the alias table key "series titleThe" is
    # mixed-case but _lookup lowercases tokens before the dict get, so
    # {Series TitleThe} never resolves and renders empty. Tracked separately.
    ctx = build_context(title="The Show", year=2020)
    assert render_sonarr_template("{Series TitleThe}", ctx) == ""


# ---- build_context ----------------------------------------------------------

def test_build_context_defaults():
    ctx = build_context(title="A<Show>", year=2020, tvdb_id="99", season=1, episode=2)
    assert ctx["title"] == "A Show"          # sanitized
    assert ctx["series_titleyear"] == "A Show (2020)"
    assert ctx["tvdb_id"] == "99"
    assert ctx["tmdb_id"] == ""
    assert ctx["release_group"] == ""
    assert ctx["is_3d"] is False
    assert ctx["season"] == 1


def test_build_context_no_year():
    ctx = build_context(title="Show", year=None)
    assert ctx["series_titleyear"] == "Show"
    assert ctx["year"] == ""
    assert ctx["year_parens"] == ""
