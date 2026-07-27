"""Macro-risk history aligned to benchmark trading sessions."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock

import pandas as pd


VALID_RANGES = ("1y", "3y", "5y", "all")
VALID_BENCHMARKS = ("SPY", "QQQ")
RANGE_YEARS = {"1y": 1, "3y": 3, "5y": 5, "all": None}

SERIES_CATALOG = {
    "DGS2": {"label": "2-year Treasury yield", "unit": "%"},
    "DGS10": {"label": "10-year Treasury yield", "unit": "%"},
    "CURVE_10Y_2Y": {"label": "10Y-2Y yield curve", "unit": "pp"},
    "CPI_YOY": {"label": "CPI year-over-year", "unit": "%"},
    "DCOILWTICO": {"label": "WTI crude oil", "unit": "USD"},
    "BAMLH0A0HYM2": {"label": "High-yield spread", "unit": "pp"},
    "VIXCLS": {"label": "VIX", "unit": "index"},
    "DTWEXBGS": {"label": "Broad dollar index", "unit": "index"},
}


class MacroHistoryService:
    def __init__(
        self,
        repository,
        macro_risk_service,
        revision_getter=lambda: 0,
        max_cache_size=8,
    ):
        self._repository = repository
        self._macro_risk_service = macro_risk_service
        self._revision_getter = revision_getter
        self._max_cache_size = max_cache_size
        self._cache = {}
        self._lock = RLock()

    def build(self, *, asof=None, range_key="3y", benchmark="SPY"):
        if range_key not in VALID_RANGES:
            raise ValueError("macro history range is invalid")
        if benchmark not in VALID_BENCHMARKS:
            raise ValueError("macro history benchmark is invalid")
        key = (
            str(asof),
            range_key,
            benchmark,
            self._revision_getter(),
            self._macro_risk_service.cache_token(),
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return deepcopy(cached)

        snapshot = self._repository.load_market_overview_snapshot(asof)
        history = snapshot.histories.get(benchmark)
        if history is None or history.empty or "Close" not in history:
            payload = self._unavailable_payload(
                snapshot.observation_date,
                range_key,
                benchmark,
            )
        else:
            payload = self._build_payload(
                snapshot.observation_date,
                history,
                range_key,
                benchmark,
            )
        with self._lock:
            self._cache[key] = deepcopy(payload)
            while len(self._cache) > self._max_cache_size:
                self._cache.pop(next(iter(self._cache)))
        return deepcopy(payload)

    def _build_payload(self, asof, history, range_key, benchmark):
        close = pd.to_numeric(history["Close"], errors="coerce").dropna()
        close = close.loc[~close.index.duplicated(keep="last")].sort_index()
        if close.empty:
            return self._unavailable_payload(asof, range_key, benchmark)
        years = RANGE_YEARS[range_key]
        if years is not None:
            start = close.index[-1] - pd.DateOffset(years=years)
            close = close.loc[close.index >= start]
        dates = tuple(value.date().isoformat() for value in close.index)
        macro_rows = self._macro_risk_service.build_history(dates)
        macro_by_date = {row["time"]: row for row in macro_rows}
        baseline = float(close.iloc[0])
        rows = []
        for timestamp, value in close.items():
            date = timestamp.date().isoformat()
            row = deepcopy(macro_by_date[date])
            row["benchmark_close"] = round(float(value), 6)
            row["benchmark_normalized"] = round(
                100.0 * float(value) / baseline,
                6,
            )
            rows.append(row)
        return {
            "asof": asof,
            "range": range_key,
            "benchmark": benchmark,
            "rows": rows,
            "series_catalog": deepcopy(SERIES_CATALOG),
            "score_thresholds": {"watch": 30, "high": 50, "severe": 70},
            "point_in_time": True,
            "unavailable_reason": None,
        }

    @staticmethod
    def _unavailable_payload(asof, range_key, benchmark):
        return {
            "asof": asof,
            "range": range_key,
            "benchmark": benchmark,
            "rows": [],
            "series_catalog": deepcopy(SERIES_CATALOG),
            "score_thresholds": {"watch": 30, "high": 50, "severe": 70},
            "point_in_time": True,
            "unavailable_reason": "benchmark_history_unavailable",
        }
