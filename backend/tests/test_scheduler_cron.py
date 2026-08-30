from datetime import datetime

import pytest

from app.services.scheduler import _expand_cron, _parse_field, cron_matches


# ---- _parse_field -----------------------------------------------------------

@pytest.mark.parametrize("field,lo,hi,expected", [
    ("*", 0, 5, {0, 1, 2, 3, 4, 5}),
    ("*/15", 0, 59, {0, 15, 30, 45}),
    ("3", 0, 23, {3}),
    ("1-5", 0, 59, {1, 2, 3, 4, 5}),
    ("1-10/3", 0, 59, {1, 4, 7, 10}),
    ("1,3,5", 0, 59, {1, 3, 5}),
    ("0,30-32", 0, 59, {0, 30, 31, 32}),
])
def test_parse_field(field, lo, hi, expected):
    assert _parse_field(field, lo, hi) == expected


@pytest.mark.parametrize("field", [
    "",           # empty field
    "1,,2",       # empty segment
    "*/0",        # non-positive step
    "5-1",        # inverted range
    "70",         # out of range (minute)
    "x",          # garbage
])
def test_parse_field_invalid(field):
    with pytest.raises(ValueError):
        _parse_field(field, 0, 59)


# ---- _expand_cron -----------------------------------------------------------

def test_expand_cron():
    minute, hour, dom, month, dow = _expand_cron("30 4 1 * 0")
    assert minute == {30}
    assert hour == {4}
    assert dom == {1}
    assert month == set(range(1, 13))
    assert dow == {0}


@pytest.mark.parametrize("expr", ["* * * *", "* * * * * *", ""])
def test_expand_cron_wrong_field_count(expr):
    with pytest.raises(ValueError):
        _expand_cron(expr)


# ---- cron_matches -----------------------------------------------------------

def test_matches_exact_minute_hour():
    assert cron_matches("30 4 * * *", datetime(2026, 1, 1, 4, 30))
    assert not cron_matches("30 4 * * *", datetime(2026, 1, 1, 4, 31))
    assert not cron_matches("30 4 * * *", datetime(2026, 1, 1, 5, 30))


def test_month_restriction():
    assert cron_matches("0 0 * 2 *", datetime(2026, 2, 1, 0, 0))
    assert not cron_matches("0 0 * 2 *", datetime(2026, 3, 1, 0, 0))


def test_dow_zero_is_sunday():
    assert cron_matches("0 0 * * 0", datetime(2026, 8, 23, 0, 0))   # Sunday
    assert not cron_matches("0 0 * * 0", datetime(2026, 8, 24, 0, 0))  # Monday
    assert cron_matches("0 0 * * 1", datetime(2026, 8, 24, 0, 0))   # Monday


def test_dom_only_restricted():
    assert cron_matches("0 0 13 * *", datetime(2026, 1, 13, 0, 0))
    assert not cron_matches("0 0 13 * *", datetime(2026, 1, 14, 0, 0))


def test_posix_dom_dow_either_match():
    # both restricted: fires on the 13th OR on Friday
    expr = "0 0 13 * 5"
    assert cron_matches(expr, datetime(2026, 1, 13, 0, 0))      # Tuesday the 13th
    assert cron_matches(expr, datetime(2026, 1, 16, 0, 0))      # Friday the 16th
    assert not cron_matches(expr, datetime(2026, 1, 14, 0, 0))  # Wednesday the 14th


def test_unrestricted_heuristic_pins_full_ranges():
    # NOTE: pins current behavior — "is restricted" is approximated by set size,
    # so an explicit full range (1-31 / 0-6) is treated as '*'. With DOM written
    # as 1-31 and DOW restricted, DOM is ignored entirely. Tracked separately.
    expr = "0 0 1-31 * 1"
    assert cron_matches(expr, datetime(2026, 8, 24, 0, 0))      # Monday -> dow wins
    assert not cron_matches(expr, datetime(2026, 8, 25, 0, 0))  # Tuesday the 25th: dom ignored
    # both written as full ranges -> matches any day
    assert cron_matches("0 0 1-31 * 0-6", datetime(2026, 8, 26, 0, 0))
