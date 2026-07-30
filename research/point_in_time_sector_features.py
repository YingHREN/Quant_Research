"""Leakage-safe monthly behavior assignments and sector-relative features."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from types import MappingProxyType

import numpy as np
import pandas as pd

from data.market_behavior import (
    RULE_VERSION as MARKET_BEHAVIOR_VERSION,
    classify_market_behavior,
)
from web.market_groups import SECTOR_ETFS


PIT_SECTOR_CANDIDATES = MappingProxyType(
    OrderedDict(
        (
            *SECTOR_ETFS.items(),
            ("semiconductor", "SOXX"),
            ("software", "IGV"),
        )
    )
)
ASSIGNMENT_RULE_VERSION = (
    f"{MARKET_BEHAVIOR_VERSION}_monthly_point_in_time_v1"
)
ASSIGNMENT_COLUMNS = (
    "ticker",
    "classification_date",
    "effective_from",
    "expires_after",
    "sector_key",
    "benchmark_ticker",
    "residual_correlation",
    "residual_beta",
    "common_days",
    "rule_version",
)


def build_monthly_behavior_assignments(
    histories: Mapping[str, pd.DataFrame],
    tickers: Iterable[str],
    *,
    start_date=None,
    minimum_observations: int = 126,
    maximum_observations: int = 252,
    maximum_age_days: int = 45,
) -> pd.DataFrame:
    """Classify each requested stock at completed month ends, then lag use."""
    prepared = _validated_histories(histories)
    requested = _normalized_tickers(tickers)
    minimum = _positive_integer(
        minimum_observations,
        "minimum_observations",
    )
    maximum = _positive_integer(
        maximum_observations,
        "maximum_observations",
    )
    age_days = _positive_integer(maximum_age_days, "maximum_age_days")
    if minimum > maximum:
        raise ValueError(
            "minimum_observations must not exceed maximum_observations"
        )
    checked_start = _optional_date(start_date)
    price_rows = {
        ticker: [
            (date.date().isoformat(), float(close))
            for date, close in frame["Close"].items()
        ]
        for ticker, frame in prepared.items()
    }
    candidates = OrderedDict(
        (key, ticker)
        for key, ticker in PIT_SECTOR_CANDIDATES.items()
        if ticker in prepared
    )
    rows = []
    for ticker in requested:
        history = prepared.get(ticker)
        if history is None or history.empty:
            continue
        sessions = history.index
        cutoffs = (
            pd.Series(sessions, index=sessions)
            .groupby(sessions.to_period("M"))
            .last()
            .sort_values()
        )
        for cutoff in cutoffs:
            future_sessions = sessions[sessions > cutoff]
            if not len(future_sessions):
                continue
            if (
                checked_start is not None
                and cutoff
                < checked_start - pd.Timedelta(days=age_days)
            ):
                continue
            result = classify_market_behavior(
                price_rows,
                ticker,
                candidates,
                sec_sector="",
                asof=cutoff.date().isoformat(),
                min_observations=minimum,
                max_observations=maximum,
            )
            if result is None:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "classification_date": cutoff,
                    "effective_from": future_sessions[0],
                    "expires_after": cutoff
                    + pd.Timedelta(days=age_days),
                    "sector_key": result.sector_key,
                    "benchmark_ticker": result.benchmark_ticker,
                    "residual_correlation": result.residual_correlation,
                    "residual_beta": result.residual_beta,
                    "common_days": int(result.common_days),
                    "rule_version": ASSIGNMENT_RULE_VERSION,
                }
            )
    if not rows:
        return pd.DataFrame(columns=ASSIGNMENT_COLUMNS)
    assignments = pd.DataFrame(rows, columns=ASSIGNMENT_COLUMNS).sort_values(
        ["ticker", "classification_date"],
        kind="mergesort",
    ).reset_index(drop=True)
    if assignments.duplicated(["ticker", "classification_date"]).any():
        raise RuntimeError("monthly behavior assignments contain duplicate keys")
    return assignments


def _validated_histories(histories):
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    prepared = {}
    for raw_ticker, source in histories.items():
        ticker = str(raw_ticker).strip().upper()
        if not ticker:
            raise ValueError("history ticker must not be empty")
        if ticker in prepared:
            raise ValueError("histories contain duplicate normalized tickers")
        if not isinstance(source, pd.DataFrame):
            raise TypeError(f"history for {ticker} must be a DataFrame")
        if "Close" not in source:
            raise ValueError(f"history for {ticker} is missing Close")
        frame = source.loc[:, ["Close"]].copy(deep=True)
        frame.index = pd.DatetimeIndex(
            pd.to_datetime(frame.index, errors="raise")
        )
        if frame.index.tz is not None:
            frame.index = frame.index.tz_localize(None)
        if frame.index.has_duplicates:
            raise ValueError(f"history for {ticker} contains duplicate dates")
        if frame.index.isna().any():
            raise ValueError(f"history for {ticker} contains missing dates")
        frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
        finite_positive = (
            np.isfinite(frame["Close"].to_numpy(dtype=float))
            & (frame["Close"].to_numpy(dtype=float) > 0.0)
        )
        frame["Close"] = frame["Close"].where(finite_positive)
        prepared[ticker] = frame.sort_index()
    return prepared


def _normalized_tickers(tickers):
    if isinstance(tickers, (str, bytes)) or not isinstance(tickers, Iterable):
        raise TypeError("tickers must be a non-string iterable")
    return tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            }
        )
    )


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _optional_date(value):
    if value is None:
        return None
    checked = pd.Timestamp(value)
    if pd.isna(checked):
        raise ValueError("start_date must be valid")
    if checked.tz is not None:
        checked = checked.tz_localize(None)
    return checked.normalize()
