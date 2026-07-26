"""Shared orchestration for warming active forecast artifact cohorts."""

from __future__ import annotations

import time

import pandas as pd


class ForecastCacheWarmer:
    def __init__(self, repository, forecast_service, max_cohorts=2):
        if not callable(getattr(repository, "list_summaries", None)):
            raise TypeError("repository must expose list_summaries()")
        if not callable(getattr(repository, "load_universe_histories", None)):
            raise TypeError("repository must expose load_universe_histories()")
        if not callable(getattr(forecast_service, "prewarm", None)):
            raise TypeError("forecast_service must expose prewarm()")
        if isinstance(max_cohorts, bool) or not isinstance(max_cohorts, int):
            raise TypeError("max_cohorts must be an integer")
        if max_cohorts <= 0:
            raise ValueError("max_cohorts must be positive")
        self._repository = repository
        self._forecast_service = forecast_service
        self._max_cohorts = max_cohorts

    def __call__(self):
        started = time.perf_counter()
        dates = sorted(
            {
                summary.latest_date
                for summary in self._repository.list_summaries()
                if not getattr(summary, "inactive", False)
            },
            reverse=True,
        )[: self._max_cohorts]
        cohorts = []
        for cohort_date in reversed(dates):
            histories = self._repository.load_universe_histories(
                pd.Timestamp(cohort_date)
            )
            result = self._forecast_service.prewarm(histories)
            cohorts.append({"asof": cohort_date, **result})
        return {
            "state": "ready",
            "cohorts": cohorts,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
