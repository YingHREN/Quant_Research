"""Build the disposable forecast artifact cache from the local price database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

import pandas as pd

from marketdata.paths import DEFAULT_MARKET_DATA_DATABASE
from web.services.forecast_artifacts import ForecastArtifactStore
from web.services.forecasts import ForecastService
from web.services.market_data import MarketDataRepository


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = PROJECT_ROOT / "data" / "analysis_cache.db"


def _parser():
    parser = argparse.ArgumentParser(
        description="Precompute the local forecast artifact cache."
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_MARKET_DATA_DATABASE),
        help="Path to the read-only prices SQLite database.",
    )
    parser.add_argument(
        "--cache",
        default=str(DEFAULT_CACHE),
        help="Path to the disposable analysis cache SQLite database.",
    )
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    started = perf_counter()
    try:
        repository = MarketDataRepository(args.database)
        freshness = repository.freshness()
        asof = freshness.get("latest_date")
        if asof is None:
            raise RuntimeError("market data has no latest date")
        histories = repository.load_universe_histories(pd.Timestamp(asof))
        store = ForecastArtifactStore(args.cache)
        service = ForecastService(artifact_store=store)
        result = service.prewarm(histories)
        payload = {
            "status": "ready",
            "asof": asof,
            "cache_path": str(Path(args.cache)),
            "elapsed_seconds": round(perf_counter() - started, 3),
            **result,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"error": "forecast_cache_build_failed"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
