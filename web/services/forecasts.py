"""Lazy, revision-scoped forecast bundles for stock-detail responses."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import threading

import pandas as pd

from web.forecasts.base import (
    ForecastEvaluation,
    ForecastResult,
    SUPPORTED_HORIZONS,
    UnavailableReason,
)
from web.forecasts.dataset import attach_forward_targets, build_feature_frame
from web.forecasts.evaluation import walk_forward_evaluate
from web.forecasts.ridge import MODEL_KEY, MODEL_VERSION, RidgeForecastProvider


DEFAULT_CACHE_SIZE = 16


class _RidgeProviderFactory:
    model_key = MODEL_KEY
    model_version = MODEL_VERSION

    def __call__(self, frame):
        return RidgeForecastProvider(frame)


class ForecastService:
    """Build and cache one point-in-time forecast bundle per exact request range.

    The lock deliberately covers cache-miss computation. Flask may serve the
    same stock concurrently; serializing a cold miss avoids fitting duplicate
    models and makes invalidation atomic with respect to cache publication.
    """

    def __init__(
        self,
        *,
        provider_factory=None,
        evaluator=walk_forward_evaluate,
        max_cache_size=DEFAULT_CACHE_SIZE,
    ):
        if isinstance(max_cache_size, bool) or not isinstance(max_cache_size, int):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        factory = (
            _RidgeProviderFactory()
            if provider_factory is None
            else provider_factory
        )
        if not callable(factory):
            raise TypeError("provider_factory must be callable")
        if not callable(evaluator):
            raise TypeError("evaluator must be callable")
        self.model_key = _required_identity(factory, "model_key")
        self.model_version = _required_identity(factory, "model_version")
        self._provider_factory = factory
        self._evaluator = evaluator
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._database_revision = 0
        self._lock = threading.RLock()

    @property
    def database_revision(self):
        with self._lock:
            return self._database_revision

    def build(self, ticker, chart_dates, histories):
        """Return a fresh JSON-ready bundle without mutating input histories."""
        ticker = _required_identity_value(ticker, "ticker")
        dates = _chart_dates(chart_dates)
        if not isinstance(histories, dict):
            try:
                histories = dict(histories)
            except (TypeError, ValueError) as exc:
                raise TypeError("histories must be a mapping") from exc
        if ticker not in histories:
            raise ValueError("histories must contain the requested ticker")

        first_date = dates[0] if dates else None
        last_date = dates[-1] if dates else None
        with self._lock:
            key = (
                self._database_revision,
                ticker,
                first_date,
                last_date,
                self.model_version,
            )
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)

            bundle = self._compute(ticker, dates, histories)
            self._cache[key] = deepcopy(bundle)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
            return deepcopy(bundle)

    def invalidate(self):
        """Advance the database revision and discard only completed bundles."""
        with self._lock:
            self._database_revision += 1
            self._cache.clear()

    def _compute(self, ticker, dates, histories):
        frame = attach_forward_targets(build_feature_frame(histories))
        provider = self._provider_factory(frame)
        _validate_provider_identity(provider, self.model_key, self.model_version)
        results = provider.forecast_series(ticker, dates, SUPPORTED_HORIZONS)
        by_date, reasons = _sparse_results(
            results,
            ticker=ticker,
            dates=dates,
            model_key=self.model_key,
            model_version=self.model_version,
        )
        model_status = "available" if by_date else "unavailable"
        unavailable_reason = None if by_date else _aggregate_reason(reasons)
        evaluations = {
            str(horizon): self._evaluator(frame, horizon, provider).to_dict()
            for horizon in SUPPORTED_HORIZONS
        }
        return {
            "forecasts": {
                "model": {
                    "key": self.model_key,
                    "version": self.model_version,
                    "status": model_status,
                    "unavailable_reason": unavailable_reason,
                },
                "horizons": list(SUPPORTED_HORIZONS),
                "by_date": by_date,
            },
            "forecast_evaluation": evaluations,
        }


def unavailable_forecast_bundle(
    model_key=MODEL_KEY,
    model_version=MODEL_VERSION,
    reason=UnavailableReason.MODEL_ERROR,
):
    """Return the stable typed fallback used when a service boundary fails."""
    model_key = _required_identity_value(model_key, "model_key")
    model_version = _required_identity_value(model_version, "model_version")
    reason_value = reason.value if isinstance(reason, UnavailableReason) else str(reason)
    evaluations = {}
    for horizon in SUPPORTED_HORIZONS:
        evaluations[str(horizon)] = ForecastEvaluation(
            horizon_sessions=horizon,
            sample_count=0,
            coverage=None,
            mae=None,
            rmse=None,
            direction_accuracy=None,
            zero_return_mae=None,
            historical_mean_mae=None,
            rank_ic=None,
            signal_bucket_returns={},
            evaluation_start=None,
            evaluation_end=None,
            model_key=model_key,
            model_version=model_version,
            unavailable_reason=reason_value,
        ).to_dict()
    return {
        "forecasts": {
            "model": {
                "key": model_key,
                "version": model_version,
                "status": "unavailable",
                "unavailable_reason": reason_value,
            },
            "horizons": list(SUPPORTED_HORIZONS),
            "by_date": {},
        },
        "forecast_evaluation": evaluations,
    }


def _chart_dates(values):
    if isinstance(values, (str, bytes)):
        raise TypeError("chart_dates must be a sequence of dates")
    try:
        raw_dates = tuple(values)
    except TypeError as exc:
        raise TypeError("chart_dates must be a sequence of dates") from exc
    dates = []
    for value in raw_dates:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("chart_dates must contain valid dates") from exc
        if pd.isna(timestamp):
            raise ValueError("chart_dates must contain valid dates")
        if timestamp.tz is not None:
            timestamp = timestamp.tz_localize(None)
        dates.append(timestamp.normalize())
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("chart_dates must be strictly increasing")
    return tuple(dates)


def _sparse_results(results, *, ticker, dates, model_key, model_version):
    if not isinstance(results, (list, tuple)):
        raise TypeError("provider forecasts must be a list or tuple")
    expected = len(dates) * len(SUPPORTED_HORIZONS)
    if len(results) != expected:
        raise ValueError("provider returned the wrong number of forecasts")
    requested_dates = set(dates)
    requested_horizons = set(SUPPORTED_HORIZONS)
    grouped = {}
    reasons = []
    identities = set()
    for result in results:
        if not isinstance(result, ForecastResult):
            raise TypeError("provider forecasts must be ForecastResult instances")
        identity = (result.asof_date, result.horizon_sessions)
        if identity in identities:
            raise ValueError("provider returned duplicate date/horizon forecasts")
        identities.add(identity)
        if (
            result.ticker != ticker
            or result.asof_date not in requested_dates
            or result.horizon_sessions not in requested_horizons
            or result.model_key != model_key
            or result.model_version != model_version
        ):
            raise ValueError("provider returned a forecast outside the request")
        if result.direction == "unavailable":
            reasons.append(result.unavailable_reason.value)
        grouped.setdefault(result.asof_date.date().isoformat(), {})[
            str(result.horizon_sessions)
        ] = result

    by_date = {}
    for date_value in dates:
        date_key = date_value.date().isoformat()
        date_results = grouped.get(date_key, {})
        if not any(result.direction != "unavailable" for result in date_results.values()):
            continue
        by_date[date_key] = {
            horizon: result.to_dict()
            for horizon, result in sorted(
                date_results.items(), key=lambda item: int(item[0])
            )
        }
    return by_date, reasons


def _aggregate_reason(reasons):
    unique = set(reasons)
    if len(unique) == 1:
        return next(iter(unique))
    return "no_available_forecasts"


def _required_identity(factory, attribute):
    try:
        value = getattr(factory, attribute)
    except AttributeError as exc:
        raise TypeError(f"provider_factory must expose {attribute}") from exc
    return _required_identity_value(value, attribute)


def _required_identity_value(value, field_name):
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_provider_identity(provider, model_key, model_version):
    if (
        getattr(provider, "model_key", None) != model_key
        or getattr(provider, "model_version", None) != model_version
    ):
        raise ValueError("provider identity does not match provider_factory")
