"""Small deterministic NYSE regular-session calendar for forecast endpoints."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd
from dateutil.easter import easter


def _observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _new_year_holiday(year: int) -> set[date]:
    value = date(year, 1, 1)
    if value.weekday() == 5:
        return set()
    return {_observed(value)}


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _regular_holidays(year: int) -> set[date]:
    holidays = {
        _nth_weekday(year, 1, calendar.MONDAY, 3),
        _nth_weekday(year, 2, calendar.MONDAY, 3),
        easter(year) - timedelta(days=2),
        _last_weekday(year, 5, calendar.MONDAY),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, calendar.MONDAY, 1),
        _nth_weekday(year, 11, calendar.THURSDAY, 4),
        _observed(date(year, 12, 25)),
    }
    holidays.update(_new_year_holiday(year))
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))
    # New Year's Day may be observed on December 31 of the preceding year.
    holidays.update(_new_year_holiday(year + 1))
    return holidays


def session_offset(
    start: pd.Timestamp,
    sessions: int,
    *,
    known_sessions: pd.DatetimeIndex | None = None,
) -> pd.Timestamp:
    """Return the date ``sessions`` NYSE sessions after ``start``.

    Recorded history wins whenever it covers the requested target. Beyond the
    recorded edge, regular NYSE weekends and holidays are applied. Unscheduled
    future exchange closures are inherently unknowable.
    """
    start = pd.Timestamp(start).normalize()
    if sessions < 0:
        raise ValueError("sessions must be non-negative")
    if sessions == 0:
        return start

    remaining = sessions
    cursor = start
    if known_sessions is not None:
        known = pd.DatetimeIndex(known_sessions).tz_localize(None).normalize()
        positions = known.get_indexer([start])
        if positions[0] >= 0:
            position = int(positions[0])
            target = position + sessions
            if target < len(known):
                return pd.Timestamp(known[target])
            available = len(known) - position - 1
            remaining -= available
            cursor = pd.Timestamp(known[-1])

    holiday_cache: dict[int, set[date]] = {}
    while remaining > 0:
        cursor += pd.Timedelta(days=1)
        current = cursor.date()
        if current.weekday() >= 5:
            continue
        holidays = holiday_cache.setdefault(current.year, _regular_holidays(current.year))
        if current in holidays:
            continue
        remaining -= 1
    return cursor.normalize()
