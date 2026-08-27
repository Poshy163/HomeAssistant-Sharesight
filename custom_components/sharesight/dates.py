"""Date arithmetic for Sharesight reporting windows.

Kept free of Home Assistant imports so the maths is unit-testable on its own
and can be reasoned about without a running core.

The only genuinely tricky piece is the financial year.  Sharesight stores a
portfolio's financial year end as a bare ``MM-DD`` string (``"06-30"`` for the
Australian/New Zealand default, ``"12-31"`` for a calendar year, ``"03-31"``
for India/Japan, ``"04-05"`` for the UK personal tax year).  The window that
contains a given day therefore has to be derived from that day, not assumed.
"""

from __future__ import annotations

from datetime import date, timedelta

# Sharesight's own default when a portfolio does not declare one.
DEFAULT_FINANCIAL_YEAR_END = "06-30"


def parse_financial_year_end(value: str | None) -> tuple[int, int]:
    """Parse a Sharesight ``MM-DD`` financial-year end into ``(month, day)``.

    Falls back to 30 June — Sharesight's own default — for anything missing or
    unparseable.  ``datetime.strptime(value, "%m-%d")`` is deliberately NOT
    used: it defaults to year 1900, which makes ``"02-29"`` raise ValueError
    (1900 was not a leap year) and which Python 3.13+ warns about and 3.15 will
    change outright.
    """
    if not value:
        return 6, 30
    parts = str(value).strip().split("-")
    if len(parts) != 2:
        return 6, 30
    try:
        month, day = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return 6, 30
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return 6, 30
    return month, day


def _clamp_to_month(year: int, month: int, day: int) -> date:
    """``date(year, month, day)`` with the day pulled back into the month.

    A 29 February financial-year end in a non-leap year becomes 28 February
    rather than raising, and a nonsensical 31st in a 30-day month becomes the
    30th.
    """
    for candidate in range(day, 0, -1):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    return date(year, month, 1)


def financial_year_bounds(financial_year_end: str | None, today: date) -> tuple[str, str]:
    """The financial year containing ``today``, as ``(start, end)`` ISO dates.

    The end date is the first occurrence of the configured ``MM-DD`` that is on
    or after ``today``; the start date is the day after the previous
    occurrence.  ``today`` is therefore always inside the returned window,
    which is the property the old June-30-only heuristic broke for every
    non-June financial year:

    >>> financial_year_bounds("12-31", date(2026, 8, 27))
    ('2026-01-01', '2026-12-31')
    >>> financial_year_bounds("06-30", date(2026, 8, 27))
    ('2026-07-01', '2027-06-30')
    >>> financial_year_bounds("04-05", date(2026, 8, 27))
    ('2026-04-06', '2027-04-05')
    """
    month, day = parse_financial_year_end(financial_year_end)

    end = _clamp_to_month(today.year, month, day)
    if end < today:
        end = _clamp_to_month(today.year + 1, month, day)

    previous_end = _clamp_to_month(end.year - 1, month, day)
    start = previous_end + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def trailing_window(today: date, days: int) -> tuple[str, str]:
    """``(start, end)`` ISO dates for the ``days``-day window ending today."""
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def year_to_date_bounds(today: date) -> tuple[str, str]:
    """``(start, end)`` for the calendar year to date."""
    return date(today.year, 1, 1).isoformat(), today.isoformat()


def week_to_date_bounds(today: date) -> tuple[str, str]:
    """``(start, end)`` for Monday-to-today.

    The end date is deliberately ``today`` and not the coming Sunday: asking
    Sharesight for a window that ends in the future gets silently clamped to
    today anyway, and sending the real end date keeps the request idempotent
    and the logs honest.
    """
    return (today - timedelta(days=today.weekday())).isoformat(), today.isoformat()


def years_ago(today: date, years: int) -> str:
    """ISO date ``years`` years before ``today`` (29 Feb clamped to 28 Feb)."""
    return _clamp_to_month(today.year - years, today.month, today.day).isoformat()


def months_ago(today: date, months: int) -> str:
    """ISO date ``months`` calendar months before ``today``, day-clamped."""
    total = (today.year * 12 + today.month - 1) - months
    year, month = divmod(total, 12)
    return _clamp_to_month(year, month + 1, today.day).isoformat()
