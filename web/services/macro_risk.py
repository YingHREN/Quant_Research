"""Read-only orchestration for point-in-time macro risk."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock

import pandas as pd

from research.macro_risk import (
    HISTORY_SERIES,
    MODEL_KEY,
    SERIES_IDS,
    build_macro_history_rows,
    build_macro_risk,
    unavailable_macro_risk,
)
from web.services.macro_store import (
    MacroDataUnavailable,
    MacroObservationStore,
)


class MacroRiskService:
    def __init__(self, database_path, max_cache_size=256):
        self._database_path = Path(database_path)
        self._store = MacroObservationStore(self._database_path)
        self._cache = {}
        self._max_cache_size = max_cache_size
        self._lock = RLock()

    def build(self, asof=None):
        cutoff = _asof_cutoff(asof)
        key = ("point", self.cache_token(), cutoff.isoformat())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return deepcopy(cached)
        try:
            rows = self._store.load_available(
                cutoff,
                series_ids=SERIES_IDS,
            )
            payload = build_macro_risk(rows, cutoff)
        except (MacroDataUnavailable, ValueError, TypeError):
            payload = unavailable_macro_risk(
                "macro_data_unavailable",
                cutoff,
            )
        with self._lock:
            self._cache[key] = deepcopy(payload)
            while len(self._cache) > self._max_cache_size:
                self._cache.pop(next(iter(self._cache)))
        return deepcopy(payload)

    def build_history(self, dates):
        normalized_dates = tuple(
            sorted(
                {
                    pd.Timestamp(value).date().isoformat()
                    for value in dates
                }
            )
        )
        if not normalized_dates:
            return []
        key = ("history", self.cache_token(), normalized_dates)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return deepcopy(cached)
        maximum = max(_asof_cutoff(value) for value in normalized_dates)
        try:
            observations = self._store.load_available(
                maximum,
                series_ids=SERIES_IDS,
            )
            payload = build_macro_history_rows(
                observations,
                normalized_dates,
            )
        except (MacroDataUnavailable, ValueError, TypeError):
            payload = [
                _unavailable_history_row(date)
                for date in normalized_dates
            ]
        with self._lock:
            self._cache[key] = deepcopy(payload)
            while len(self._cache) > self._max_cache_size:
                self._cache.pop(next(iter(self._cache)))
        return deepcopy(payload)

    def attach_chart_rows(self, chart, dates=None):
        selected_dates = (
            None
            if dates is None
            else {str(value) for value in dates if value is not None}
        )
        scoped = [
            row
            for row in chart
            if selected_dates is None or str(row.get("time")) in selected_dates
        ]
        if not scoped:
            return chart
        maximum = max(
            _asof_cutoff(row.get("time"))
            for row in scoped
        )
        try:
            observations = self._store.load_available(
                maximum,
                series_ids=SERIES_IDS,
            )
        except MacroDataUnavailable:
            observations = None
        for row in scoped:
            payload = (
                build_macro_risk(observations, _asof_cutoff(row.get("time")))
                if observations is not None
                else unavailable_macro_risk(
                    "macro_data_unavailable",
                    _asof_cutoff(row.get("time")),
                )
            )
            row.update(
                {
                    "macro_risk_model_key": MODEL_KEY,
                    "macro_risk_model_version": payload["model_version"],
                    "macro_risk_score": payload["score"],
                    "macro_risk_coverage": payload["coverage"],
                    "macro_risk_state": payload["state"],
                    "macro_risk_conditions": list(payload["conditions"]),
                    "macro_risk_components": deepcopy(payload["components"]),
                    "macro_risk_evidence": deepcopy(payload["evidence"]),
                    "macro_risk_unavailable_reason": payload[
                        "unavailable_reason"
                    ],
                }
            )
        return chart

    def cache_token(self):
        try:
            stat = self._database_path.stat()
        except OSError:
            return ("missing",)
        return (stat.st_mtime_ns, stat.st_size)


def _asof_cutoff(asof):
    if asof is None:
        return pd.Timestamp.now(tz="UTC")
    value = pd.Timestamp(asof)
    if value.tz is None:
        value = value + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        return value.tz_localize("UTC")
    return value.tz_convert("UTC")


def _unavailable_history_row(date):
    risk = unavailable_macro_risk(
        "macro_data_unavailable",
        _asof_cutoff(date),
    )
    return {
        "time": pd.Timestamp(date).date().isoformat(),
        "score": risk["score"],
        "coverage": risk["coverage"],
        "state": risk["state"],
        "components": risk["components"],
        "series": {
            key: {
                "value": None,
                "observation_date": None,
                "available_at": None,
                "series_ids": [],
            }
            for key in HISTORY_SERIES
        },
        "evidence": risk["evidence"],
        "unavailable_reason": risk["unavailable_reason"],
    }
