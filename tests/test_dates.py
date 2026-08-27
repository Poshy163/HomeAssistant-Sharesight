"""Tests for the Sharesight reporting-window date maths.

These are pure functions with no Home Assistant dependency, so they run fast
and pin down the behaviour that used to be wrong for every portfolio whose
financial year does not end on 30 June.
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.sharesight.dates import (
    financial_year_bounds,
    months_ago,
    parse_financial_year_end,
    trailing_window,
    week_to_date_bounds,
    year_to_date_bounds,
    years_ago,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("06-30", (6, 30)),
        ("12-31", (12, 31)),
        ("03-31", (3, 31)),
        ("04-05", (4, 5)),
        # 29 February parses instead of raising, unlike strptime("%m-%d").
        ("02-29", (2, 29)),
        # Anything unusable falls back to Sharesight's own default.
        (None, (6, 30)),
        ("", (6, 30)),
        ("nonsense", (6, 30)),
        ("13-01", (6, 30)),
        ("06-32", (6, 30)),
        ("2026-06-30", (6, 30)),
    ],
)
def test_parse_financial_year_end(value, expected) -> None:
    assert parse_financial_year_end(value) == expected


@pytest.mark.parametrize(
    ("fy_end", "today", "expected"),
    [
        # Australian / New Zealand default.
        ("06-30", date(2026, 8, 27), ("2026-07-01", "2027-06-30")),
        ("06-30", date(2026, 2, 15), ("2025-07-01", "2026-06-30")),
        ("06-30", date(2026, 6, 30), ("2025-07-01", "2026-06-30")),
        ("06-30", date(2026, 7, 1), ("2026-07-01", "2027-06-30")),
        # Calendar year (US and most of Europe).  The old code put this window
        # entirely in the future for seven months of every year.
        ("12-31", date(2026, 8, 27), ("2026-01-01", "2026-12-31")),
        ("12-31", date(2026, 1, 1), ("2026-01-01", "2026-12-31")),
        ("12-31", date(2026, 12, 31), ("2026-01-01", "2026-12-31")),
        # India / Japan.
        ("03-31", date(2026, 8, 27), ("2026-04-01", "2027-03-31")),
        ("03-31", date(2026, 2, 1), ("2025-04-01", "2026-03-31")),
        # UK personal tax year.
        ("04-05", date(2026, 8, 27), ("2026-04-06", "2027-04-05")),
        ("04-05", date(2026, 4, 5), ("2025-04-06", "2026-04-05")),
        ("04-05", date(2026, 4, 6), ("2026-04-06", "2027-04-05")),
        # September end - the other case the old heuristic mis-placed.
        ("09-30", date(2026, 8, 27), ("2025-10-01", "2026-09-30")),
        ("09-30", date(2026, 10, 1), ("2026-10-01", "2027-09-30")),
        # Missing setting falls back to 30 June.
        (None, date(2026, 8, 27), ("2026-07-01", "2027-06-30")),
    ],
)
def test_financial_year_bounds(fy_end, today, expected) -> None:
    assert financial_year_bounds(fy_end, today) == expected


@pytest.mark.parametrize(
    ("fy_end", "today"),
    [
        (fy, date(2026, month, day))
        for fy in ("06-30", "12-31", "03-31", "04-05", "09-30", "02-28", "02-29")
        for month, day in (
            (1, 1),
            (2, 28),
            (3, 31),
            (4, 5),
            (4, 6),
            (6, 30),
            (7, 1),
            (9, 30),
            (10, 1),
            (12, 31),
        )
    ],
)
def test_financial_year_always_contains_today(fy_end, today) -> None:
    """The invariant the old implementation violated."""
    start, end = financial_year_bounds(fy_end, today)
    assert start <= today.isoformat() <= end


def test_financial_year_leap_day_end_does_not_raise() -> None:
    """A 29 February year end clamps rather than raising in a non-leap year."""
    start, end = financial_year_bounds("02-29", date(2026, 5, 1))
    assert (start, end) == ("2026-03-01", "2027-02-28")

    start, end = financial_year_bounds("02-29", date(2028, 1, 1))
    assert end == "2028-02-29"


def test_trailing_and_calendar_windows() -> None:
    today = date(2026, 8, 27)
    assert trailing_window(today, 45) == ("2026-07-13", "2026-08-27")
    assert year_to_date_bounds(today) == ("2026-01-01", "2026-08-27")
    # 27 August 2026 is a Thursday; the week starts on the Monday.
    assert week_to_date_bounds(today) == ("2026-08-24", "2026-08-27")


def test_week_to_date_end_is_never_in_the_future() -> None:
    for day in range(1, 32):
        today = date(2026, 8, day)
        start, end = week_to_date_bounds(today)
        assert end == today.isoformat()
        assert start <= end


def test_years_and_months_ago() -> None:
    today = date(2026, 8, 27)
    assert years_ago(today, 1) == "2025-08-27"
    assert years_ago(today, 5) == "2021-08-27"
    assert months_ago(today, 3) == "2026-05-27"
    assert months_ago(today, 6) == "2026-02-27"
    assert months_ago(today, 12) == "2025-08-27"
    # Leap day clamps in a non-leap year.
    assert years_ago(date(2028, 2, 29), 1) == "2027-02-28"
    assert months_ago(date(2026, 3, 31), 1) == "2026-02-28"
