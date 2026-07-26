#!/usr/bin/env python3
"""Fetch point-in-time initial-release macro observations from FRED/ALFRED."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from research.macro_risk import SERIES_IDS
from web.services.macro_store import MacroObservationStore


FRED_OBSERVATIONS_URL = (
    "https://api.stlouisfed.org/fred/series/observations"
)
DEFAULT_DATABASE = Path(__file__).resolve().parent / "data" / "macro_data.db"


def fetch_initial_release_observations(
    api_key,
    series_id,
    *,
    observation_start,
    observation_end,
    opener=urlopen,
):
    """Fetch output_type=4 so historical values use their initial release."""
    query = urlencode(
        {
            "api_key": api_key,
            "file_type": "json",
            "series_id": series_id,
            "observation_start": observation_start,
            "observation_end": observation_end,
            "realtime_start": "1776-07-04",
            "realtime_end": "9999-12-31",
            "output_type": 4,
        }
    )
    request = Request(
        f"{FRED_OBSERVATIONS_URL}?{query}",
        headers={"User-Agent": "stock-screener-macro/1.0"},
    )
    with opener(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = []
    for observation in payload.get("observations", ()):
        raw_value = observation.get("value")
        if raw_value in (None, "", "."):
            continue
        realtime_start = observation["realtime_start"]
        rows.append(
            {
                "series_id": series_id,
                "observation_date": observation["date"],
                # FRED exposes a real-time date, not an intraday release time.
                # End-of-UTC-day availability prevents same-day lookahead.
                "available_at": f"{realtime_start}T23:59:59+00:00",
                "value": float(raw_value),
                "realtime_start": realtime_start,
                "realtime_end": observation.get(
                    "realtime_end",
                    "9999-12-31",
                ),
                "source": "ALFRED_initial_release",
            }
        )
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
        choices=SERIES_IDS,
        default=list(SERIES_IDS),
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
