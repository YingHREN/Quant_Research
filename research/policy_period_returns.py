"""Descriptive, non-predictive returns for audited Fed policy periods."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


POLICY_ETFS = (
    "SPY",
    "QQQ",
    "XLK",
    "XLC",
    "XLY",
    "XLP",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLB",
    "XLRE",
    "XLU",
)

_METRIC_COLUMNS = (
    "total_return",
    "annualized_return",
    "relative_spy_return",
    "max_drawdown",
    "positive_month_ratio",
)


def describe_policy_periods(
    periods,
    histories,
    asof,
    annual_sessions=252,
):
    """Calculate historical descriptions without assigning a model score."""
    frame = pd.DataFrame(periods).copy()
    required = {
        "period_id",
        "catalog_version",
        "label_zh",
        "label_en",
        "start_date",
        "end_date",
        "available_at",
    }
    if not required.issubset(frame.columns):
        missing = sorted(required - set(frame.columns))
        raise ValueError(
            "policy periods missing: " + ", ".join(missing)
        )
    if not isinstance(annual_sessions, int) or annual_sessions <= 0:
        raise ValueError("annual_sessions must be a positive integer")
    cutoff = _utc_timestamp(asof)
    prepared = {
        str(ticker).upper(): _prepare_history(history, cutoff)
        for ticker, history in histories.items()
    }
    rows = []
    for period in frame.itertuples(index=False):
        start = pd.Timestamp(period.start_date).normalize()
        end = (
            None
            if pd.isna(period.end_date) or period.end_date in (None, "")
            else pd.Timestamp(period.end_date).normalize()
        )
        available = _utc_timestamp(period.available_at)
        for ticker in POLICY_ETFS:
            base = {
                "period_id": str(period.period_id),
                "catalog_version": str(period.catalog_version),
                "label_zh": str(period.label_zh),
                "label_en": str(period.label_en),
                "start_date": start.date().isoformat(),
                "end_date": (
                    None if end is None else end.date().isoformat()
                ),
                "ticker": ticker,
                "status": None,
                "first_session": None,
                "last_session": None,
                "session_count": 0,
                **{column: np.nan for column in _METRIC_COLUMNS},
            }
            if available > cutoff:
                base["status"] = "unavailable_at_asof"
            elif end is None or end > cutoff.tz_localize(None).normalize():
                base["status"] = "incomplete"
            else:
                _populate_complete_period(
                    base,
                    ticker=ticker,
                    start=start,
                    end=end,
                    histories=prepared,
                    annual_sessions=annual_sessions,
                )
            rows.append(base)
    return pd.DataFrame(rows)


def _populate_complete_period(
    row,
    *,
    ticker,
    start,
    end,
    histories,
    annual_sessions,
):
    history = histories.get(ticker)
    if history is None or history.empty:
        row["status"] = "missing_history"
        return
    if history.index.min() > end:
        row["status"] = "not_listed"
        return
    scoped = history.loc[
        (history.index >= start) & (history.index <= end)
    ]
    if len(scoped) < 2:
        row["status"] = "insufficient_history"
        return

    values = scoped["adjusted_close"]
    row["status"] = "complete"
    row["first_session"] = scoped.index[0].date().isoformat()
    row["last_session"] = scoped.index[-1].date().isoformat()
    row["session_count"] = int(len(scoped))
    row["total_return"] = float(values.iloc[-1] / values.iloc[0] - 1.0)
    elapsed_sessions = max(len(values) - 1, 1)
    row["annualized_return"] = float(
        math.pow(
            float(values.iloc[-1] / values.iloc[0]),
            annual_sessions / elapsed_sessions,
        )
        - 1.0
    )
    cumulative = values / values.iloc[0]
    row["max_drawdown"] = float(
        (cumulative / cumulative.cummax() - 1.0).min()
    )
    monthly = values.resample("ME").last().dropna()
    monthly_returns = monthly.pct_change().dropna()
    row["positive_month_ratio"] = (
        float((monthly_returns > 0).mean())
        if not monthly_returns.empty
        else np.nan
    )
    spy = histories.get("SPY")
    if spy is None or spy.empty:
        return
    common = scoped.index.intersection(
        spy.loc[(spy.index >= start) & (spy.index <= end)].index
    )
    if len(common) < 2:
        return
    ticker_return = (
        scoped.loc[common[-1], "adjusted_close"]
        / scoped.loc[common[0], "adjusted_close"]
    )
    spy_return = (
        spy.loc[common[-1], "adjusted_close"]
        / spy.loc[common[0], "adjusted_close"]
    )
    row["relative_spy_return"] = float(ticker_return / spy_return - 1.0)


def _prepare_history(history, cutoff):
    frame = pd.DataFrame(history).copy()
    if frame.empty:
        return pd.DataFrame(columns=["adjusted_close"])
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("policy history index must be a DatetimeIndex")
    normalized_index = frame.index.tz_localize(None).normalize()
    frame.index = normalized_index
    frame = frame.loc[
        frame.index <= cutoff.tz_localize(None).normalize()
    ].copy()
    if frame.empty:
        return pd.DataFrame(columns=["adjusted_close"])
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(
            "policy history dates must be unique and increasing"
        )
    column = "Adj Close" if "Adj Close" in frame.columns else "Close"
    if column not in frame.columns:
        raise ValueError(
            "policy history requires adjusted Close or Adj Close"
        )
    values = pd.to_numeric(frame[column], errors="coerce")
    if (
        values.isna().any()
        or not np.isfinite(values.to_numpy(dtype=float)).all()
        or (values <= 0).any()
    ):
        raise ValueError(
            "policy history adjusted closes must be finite and positive"
        )
    return pd.DataFrame(
        {"adjusted_close": values.astype(float)},
        index=frame.index,
    )


def _utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp
