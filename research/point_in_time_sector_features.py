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
FEATURE_COLUMNS = (
    "pit_sector_relative_strength_20",
    "pit_stock_sector_relative_strength_20",
    "pit_sector_assignment_age_days",
    "pit_sector_residual_correlation",
    "pit_sector_assignment_available",
    "pit_sector_key",
    "pit_sector_benchmark",
    "pit_sector_unavailable_reason",
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


def build_point_in_time_sector_features(
    histories: Mapping[str, pd.DataFrame],
    assignments: pd.DataFrame,
    observation_index: pd.MultiIndex,
) -> pd.DataFrame:
    """Build exact-endpoint 20-session sector features without lookahead."""
    prepared = _validated_histories(histories)
    checked_assignments = _validated_assignments(assignments)
    checked_index = _validated_observation_index(observation_index)
    rows = []
    grouped_assignments = {
        ticker: frame.reset_index(drop=True)
        for ticker, frame in checked_assignments.groupby(
            "ticker",
            sort=False,
        )
    }
    for ticker, observation_date in checked_index:
        row = _empty_feature_row()
        history = prepared.get(ticker)
        ticker_assignments = grouped_assignments.get(ticker)
        if ticker_assignments is None or ticker_assignments.empty:
            row["pit_sector_unavailable_reason"] = (
                "no_effective_assignment"
            )
            rows.append(row)
            continue
        effective_values = ticker_assignments[
            "effective_from"
        ].to_numpy(dtype="datetime64[ns]")
        assignment_position = int(
            np.searchsorted(
                effective_values,
                observation_date.to_datetime64(),
                side="right",
            )
            - 1
        )
        if assignment_position < 0:
            row["pit_sector_unavailable_reason"] = (
                "no_effective_assignment"
            )
            rows.append(row)
            continue
        assignment = ticker_assignments.iloc[assignment_position]
        row["pit_sector_key"] = assignment["sector_key"]
        row["pit_sector_benchmark"] = assignment["benchmark_ticker"]
        row["pit_sector_residual_correlation"] = assignment[
            "residual_correlation"
        ]
        row["pit_sector_assignment_age_days"] = int(
            (observation_date - assignment["classification_date"]).days
        )
        if observation_date > assignment["expires_after"]:
            row["pit_sector_unavailable_reason"] = "stale_assignment"
            rows.append(row)
            continue
        benchmark = assignment["benchmark_ticker"]
        if benchmark not in prepared:
            row["pit_sector_unavailable_reason"] = "unknown_benchmark"
            rows.append(row)
            continue
        if "QQQ" not in prepared:
            row["pit_sector_unavailable_reason"] = "unknown_qqq"
            rows.append(row)
            continue
        if history is None or observation_date not in history.index:
            row["pit_sector_unavailable_reason"] = (
                "missing_stock_endpoint"
            )
            rows.append(row)
            continue
        stock_position = int(history.index.get_loc(observation_date))
        if stock_position < 20:
            row["pit_sector_unavailable_reason"] = (
                "insufficient_stock_sessions"
            )
            rows.append(row)
            continue
        start_date = history.index[stock_position - 20]
        benchmark_history = prepared[benchmark]
        if (
            start_date not in benchmark_history.index
            or observation_date not in benchmark_history.index
        ):
            row["pit_sector_unavailable_reason"] = (
                "missing_benchmark_endpoint"
            )
            rows.append(row)
            continue
        qqq_history = prepared["QQQ"]
        if (
            start_date not in qqq_history.index
            or observation_date not in qqq_history.index
        ):
            row["pit_sector_unavailable_reason"] = "missing_qqq_endpoint"
            rows.append(row)
            continue
        stock_return = _exact_return(history, start_date, observation_date)
        benchmark_return = _exact_return(
            benchmark_history,
            start_date,
            observation_date,
        )
        qqq_return = _exact_return(
            qqq_history,
            start_date,
            observation_date,
        )
        if not all(
            np.isfinite(value)
            for value in (stock_return, benchmark_return, qqq_return)
        ):
            row["pit_sector_unavailable_reason"] = (
                "nonfinite_exact_return"
            )
            rows.append(row)
            continue
        row["pit_sector_relative_strength_20"] = (
            benchmark_return - qqq_return
        )
        row["pit_stock_sector_relative_strength_20"] = (
            stock_return - benchmark_return
        )
        row["pit_sector_assignment_available"] = True
        row["pit_sector_unavailable_reason"] = ""
        rows.append(row)
    result = pd.DataFrame(rows, columns=FEATURE_COLUMNS, index=checked_index)
    result["pit_sector_assignment_available"] = result[
        "pit_sector_assignment_available"
    ].astype(bool)
    return result


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


def _validated_assignments(assignments):
    if not isinstance(assignments, pd.DataFrame):
        raise TypeError("assignments must be a DataFrame")
    missing = set(ASSIGNMENT_COLUMNS) - set(assignments.columns)
    if missing:
        raise ValueError(
            "assignments are missing columns: "
            + ", ".join(sorted(missing))
        )
    checked = assignments.loc[:, ASSIGNMENT_COLUMNS].copy(deep=True)
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["benchmark_ticker"] = (
        checked["benchmark_ticker"].astype(str).str.strip().str.upper()
    )
    checked["sector_key"] = checked["sector_key"].astype(str).str.strip()
    for column in (
        "classification_date",
        "effective_from",
        "expires_after",
    ):
        checked[column] = pd.to_datetime(checked[column], errors="raise")
        if checked[column].dt.tz is not None:
            checked[column] = checked[column].dt.tz_localize(None)
        checked[column] = checked[column].dt.normalize()
    if checked["ticker"].eq("").any():
        raise ValueError("assignment ticker must not be empty")
    if checked.duplicated(["ticker", "classification_date"]).any():
        raise ValueError("assignments contain duplicate classification dates")
    if checked.duplicated(["ticker", "effective_from"]).any():
        raise ValueError("assignments contain duplicate effective dates")
    if (
        checked["effective_from"] <= checked["classification_date"]
    ).any():
        raise ValueError("assignment must start after classification date")
    if (checked["expires_after"] < checked["effective_from"]).any():
        raise ValueError("assignment expires before it becomes effective")
    checked["residual_correlation"] = pd.to_numeric(
        checked["residual_correlation"],
        errors="raise",
    )
    if not np.isfinite(
        checked["residual_correlation"].to_numpy(dtype=float)
    ).all():
        raise ValueError("assignment residual correlation must be finite")
    return checked.sort_values(
        ["ticker", "effective_from"],
        kind="mergesort",
    ).reset_index(drop=True)


def _validated_observation_index(observation_index):
    if not isinstance(observation_index, pd.MultiIndex):
        raise TypeError("observation_index must be a MultiIndex")
    if observation_index.nlevels != 2:
        raise ValueError("observation_index must have exactly two levels")
    tuples = []
    for raw_ticker, raw_date in observation_index.tolist():
        ticker = str(raw_ticker).strip().upper()
        if not ticker:
            raise ValueError("observation ticker must not be empty")
        date = pd.Timestamp(raw_date)
        if pd.isna(date):
            raise ValueError("observation date must be valid")
        if date.tz is not None:
            date = date.tz_localize(None)
        tuples.append((ticker, date.normalize()))
    checked = pd.MultiIndex.from_tuples(
        tuples,
        names=["ticker", "observation_date"],
    )
    if checked.has_duplicates:
        raise ValueError("observation_index contains duplicate keys")
    return checked


def _empty_feature_row():
    return {
        "pit_sector_relative_strength_20": np.nan,
        "pit_stock_sector_relative_strength_20": np.nan,
        "pit_sector_assignment_age_days": np.nan,
        "pit_sector_residual_correlation": np.nan,
        "pit_sector_assignment_available": False,
        "pit_sector_key": "",
        "pit_sector_benchmark": "",
        "pit_sector_unavailable_reason": "",
    }


def _exact_return(history, start_date, end_date):
    start = float(history.at[start_date, "Close"])
    end = float(history.at[end_date, "Close"])
    if not np.isfinite(start) or not np.isfinite(end) or start <= 0.0:
        return np.nan
    return end / start - 1.0


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
