#!/usr/bin/env python3
"""Fetch point-in-time initial-release macro observations from FRED/ALFRED."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from research.macro_risk import SERIES_IDS
from research.policy_context import POLICY_SERIES_IDS
from web.services.macro_store import MacroObservationStore


FRED_OBSERVATIONS_URL = (
    "https://api.stlouisfed.org/fred/series/observations"
)
DEFAULT_DATABASE = Path(__file__).resolve().parent / "data" / "macro_data.db"
REALTIME_CHUNK_YEARS = 5
ALL_MACRO_SERIES_IDS = tuple(
    dict.fromkeys((*SERIES_IDS, *POLICY_SERIES_IDS))
)


def _add_years(value, years):
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def fetch_initial_release_observations(
    api_key,
    series_id,
    *,
    observation_start,
    observation_end,
    opener=urlopen,
):
    """Fetch output_type=4 so historical values use their initial release."""
    rows = []
    realtime_start = date.fromisoformat(observation_start)
    realtime_limit = date.fromisoformat(observation_end)
    while realtime_start <= realtime_limit:
        next_start = _add_years(realtime_start, REALTIME_CHUNK_YEARS)
        realtime_end = min(
            next_start - timedelta(days=1),
            realtime_limit,
        )
        request_realtime_end = (
            "9999-12-31"
            if realtime_end == realtime_limit
            else realtime_end.isoformat()
        )
        query = urlencode(
            {
                "api_key": api_key,
                "file_type": "json",
                "series_id": series_id,
                "observation_start": observation_start,
                "observation_end": observation_end,
                "realtime_start": realtime_start.isoformat(),
                "realtime_end": request_realtime_end,
                "output_type": 4,
            }
        )
        request = Request(
            f"{FRED_OBSERVATIONS_URL}?{query}",
            headers={"User-Agent": "stock-screener-macro/1.0"},
        )
        try:
            with opener(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            payload = json.loads(error.read().decode("utf-8"))
            message = payload.get("error_message", "")
            if (
                error.code != 400
                or "does not exist in ALFRED" not in message
            ):
                raise
            payload = {"observations": []}
        for observation in payload.get("observations", ()):
            raw_value = observation.get("value")
            if raw_value in (None, "", "."):
                continue
            released_at = observation["realtime_start"]
            rows.append(
                {
                    "series_id": series_id,
                    "observation_date": observation["date"],
                    # FRED exposes a date, not an intraday release time.
                    # End-of-UTC-day availability prevents same-day lookahead.
                    "available_at": f"{released_at}T23:59:59+00:00",
                    "value": float(raw_value),
                    "realtime_start": released_at,
                    "realtime_end": observation.get(
                        "realtime_end",
                        "9999-12-31",
                    ),
                    "source": "ALFRED_initial_release",
                    "revision_policy": "initial_release_only",
                }
            )
        realtime_start = realtime_end + timedelta(days=1)
    return rows


def main(argv=None, *, fetcher=fetch_initial_release_observations):
    parser = argparse.ArgumentParser(
        description=(
            "Fetch release-aware FRED/ALFRED initial observations into "
            "the dashboard's independent macro database."
        )
    )
    parser.add_argument("--database", default=os.fspath(DEFAULT_DATABASE))
    parser.add_argument(
        "--api-key",
        default=os.environ.get("FRED_API_KEY"),
        help="FRED API key; defaults to FRED_API_KEY.",
    )
    parser.add_argument(
        "--series",
        nargs="+",
        choices=ALL_MACRO_SERIES_IDS,
        default=list(ALL_MACRO_SERIES_IDS),
    )
    parser.add_argument(
        "--start",
        default=(date.today() - timedelta(days=365 * 12)).isoformat(),
    )
    parser.add_argument("--end", default=date.today().isoformat())
    arguments = parser.parse_args(argv)
    if not arguments.api_key:
        parser.error("FRED_API_KEY or --api-key is required")

    store = MacroObservationStore(arguments.database)
    store.initialize()
    total = 0
    for series_id in arguments.series:
        rows = fetcher(
            arguments.api_key,
            series_id,
            observation_start=arguments.start,
            observation_end=arguments.end,
        )
        total += store.upsert(rows)
    print(
        f"macro observations upserted: {total}; "
        f"series: {len(arguments.series)}; database: {arguments.database}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
