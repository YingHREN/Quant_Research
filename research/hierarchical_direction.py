"""Leakage-safe recency and hierarchy helpers for offline direction research."""

from __future__ import annotations

from numbers import Integral

import numpy as np
import pandas as pd

from data.market_behavior import (
    RULE_VERSION as MARKET_BEHAVIOR_VERSION,
    classify_market_behavior,
)
from web.market_groups import SECTOR_ETFS


HALF_LIFE_BY_HORIZON = {5: 126, 20: 252, 60: 504}
DIRECTION_CLASSES = ("down", "neutral", "up")


def recency_class_weights(
    index,
    labels,
    horizon,
    *,
    minimum_effective_samples=1_000,
    minimum_class_effective_samples=100,
):
    """Return fixed recency × class-balance weights and auditable diagnostics."""
    checked_horizon = _checked_horizon(horizon)
    checked_total = _positive_number(
        minimum_effective_samples,
        "minimum_effective_samples",
    )
    checked_class = _positive_number(
        minimum_class_effective_samples,
        "minimum_class_effective_samples",
    )
    dates = _observation_dates(index)
    values = np.asarray(labels, dtype=object).copy()
    if len(dates) != len(values) or not len(values):
        raise ValueError("index and labels must have the same non-zero length")
    unknown = sorted(set(map(str, values)).difference(DIRECTION_CLASSES))
    if unknown:
        raise ValueError(f"direction labels must be supported: {unknown}")

    unique_dates = pd.DatetimeIndex(sorted(pd.unique(dates)))
    positions = {value: offset for offset, value in enumerate(unique_dates)}
    latest = len(unique_dates) - 1
    ages = np.asarray(
        [latest - positions[pd.Timestamp(value)] for value in dates],
        dtype=float,
    )
    time_weights = np.power(
        0.5,
        ages / float(HALF_LIFE_BY_HORIZON[checked_horizon]),
    )
    class_time_sums = {
        label: float(time_weights[values == label].sum())
        for label in DIRECTION_CLASSES
        if np.any(values == label)
    }
    if not class_time_sums or any(value <= 0.0 for value in class_time_sums.values()):
        raise ValueError("direction labels must have positive effective counts")
    total_time = float(time_weights.sum())
    class_count = len(class_time_sums)
    multipliers = {
        label: total_time / (class_count * value)
        for label, value in class_time_sums.items()
    }
    combined = time_weights * np.asarray(
        [multipliers[str(label)] for label in values],
        dtype=float,
    )
    combined /= float(combined.mean())

    class_effective = {
        label: _kish_effective_sample(combined[values == label])
        for label in class_time_sums
    }
    diagnostics = {
        "status": "available",
        "reason": None,
        "horizon": checked_horizon,
        "half_life_sessions": HALF_LIFE_BY_HORIZON[checked_horizon],
        "raw_sample_count": len(combined),
        "weight_sum": float(combined.sum()),
        "effective_sample_size": _kish_effective_sample(combined),
        "class_effective_sample_size": class_effective,
        "class_weight_sum": {
            label: float(combined[values == label].sum())
            for label in class_time_sums
        },
        "minimum_weight": float(combined.min()),
        "median_weight": float(np.median(combined)),
        "maximum_weight": float(combined.max()),
    }
    if diagnostics["effective_sample_size"] < checked_total:
        diagnostics["status"] = "unavailable"
        diagnostics["reason"] = "insufficient_effective_samples"
        return None, diagnostics
    if any(value < checked_class for value in class_effective.values()):
        diagnostics["status"] = "unavailable"
        diagnostics["reason"] = "insufficient_class_effective_samples"
        return None, diagnostics
    return combined, diagnostics


def freeze_behavior_groups(
    histories,
    tickers,
    cutoff,
    *,
    sector_etfs=SECTOR_ETFS,
):
    """Freeze price-behavior groups using only prices visible by cutoff."""
    checked_cutoff = pd.Timestamp(cutoff)
    if pd.isna(checked_cutoff):
        raise ValueError("cutoff must be a valid date")
    if checked_cutoff.tz is not None:
        checked_cutoff = checked_cutoff.tz_localize(None)
    checked_cutoff = checked_cutoff.normalize()
    requested = tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            }
        )
    )
    price_rows = {
        str(ticker).strip().upper(): _price_rows(frame, checked_cutoff)
        for ticker, frame in histories.items()
        if str(ticker).strip()
    }
    normalized_etfs = {
        str(sector): str(ticker).strip().upper()
        for sector, ticker in sector_etfs.items()
        if str(sector).strip() and str(ticker).strip()
    }
    groups = {}
    common_days = {}
    sector_counts = {}
    for ticker in requested:
        result = classify_market_behavior(
            price_rows,
            ticker,
            normalized_etfs,
            sec_sector="",
            asof=checked_cutoff.date().isoformat(),
            min_observations=126,
            max_observations=252,
        )
        if result is None:
            groups[ticker] = None
            continue
        groups[ticker] = result.sector_key
        common_days[ticker] = result.common_days
        sector_counts[result.sector_key] = (
            sector_counts.get(result.sector_key, 0) + 1
        )
    classified = sum(group is not None for group in groups.values())
    diagnostics = {
        "cutoff": checked_cutoff.date().isoformat(),
        "rule_version": MARKET_BEHAVIOR_VERSION,
        "requested_count": len(requested),
        "classified_count": classified,
        "unavailable_count": len(requested) - classified,
        "sector_counts": dict(sorted(sector_counts.items())),
        "common_days": dict(sorted(common_days.items())),
    }
    return groups, diagnostics


def _price_rows(frame, cutoff):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    column = "Adj Close" if "Adj Close" in frame else "Close"
    if column not in frame:
        return []
    dates = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    rows = []
    for raw_date, value in zip(dates, values):
        if (
            pd.notna(raw_date)
            and raw_date.normalize() <= cutoff
            and np.isfinite(value)
            and value > 0.0
        ):
            rows.append((raw_date.date().isoformat(), float(value)))
    return rows


def _observation_dates(index):
    if isinstance(index, pd.MultiIndex):
        if "observation_date" not in index.names:
            raise ValueError("index must contain observation_date")
        raw = index.get_level_values("observation_date")
    else:
        raw = index
    dates = pd.DatetimeIndex(pd.to_datetime(raw, errors="coerce"))
    if dates.isna().any():
        raise ValueError("observation dates must be valid")
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    return dates.normalize()


def _checked_horizon(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, Integral)
        or int(value) not in HALF_LIFE_BY_HORIZON
    ):
        raise ValueError("horizon must be a supported session count")
    return int(value)


def _positive_number(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a positive number")
    checked = float(value)
    if not np.isfinite(checked) or checked <= 0.0:
        raise ValueError(f"{name} must be a positive number")
    return checked


def _kish_effective_sample(weights):
    values = np.asarray(weights, dtype=float)
    denominator = float(np.square(values).sum())
    if denominator <= 0.0:
        return 0.0
    return float(values.sum() ** 2 / denominator)
