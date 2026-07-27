"""Point-in-time CAN SLIM-style market environment gate.

The gate is deliberately separate from return forecasts.  It answers whether
the broad market currently permits a formal long candidate, while preserving
the raw direction produced by forecasting models.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


MARKET_GATE_VERSION = "market_regime_gate_v1"
MINIMUM_COMMON_SESSIONS = 200
DISTRIBUTION_WINDOW_SESSIONS = 25
DISTRIBUTION_REMOVAL_GAIN = 0.05

MARKET_GATE_COLUMNS = (
    "gate_state",
    "market_state",
    "market_state_start",
    "follow_through_date",
    "rally_day_count",
    "qqq_distribution_days",
    "spy_distribution_days",
    "distribution_days",
    "breadth_above_ema20",
    "breadth_above_sma50",
    "breadth_count",
    "qqq_close",
    "qqq_ema20",
    "qqq_sma50",
    "qqq_sma200",
    "qqq_return_5",
    "qqq_return_20",
    "qqq_drawdown_63",
    "spy_close",
    "spy_ema20",
    "spy_sma50",
    "spy_sma200",
    "reason_codes",
    "gate_version",
)


def build_market_gate_frame(
    histories: Mapping,
    *,
    minimum_breadth: int = 20,
) -> pd.DataFrame:
    """Build a causal market-state frame for every common SPY/QQQ session."""
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    if not isinstance(minimum_breadth, int) or minimum_breadth < 0:
        raise ValueError("minimum_breadth must be a non-negative integer")

    qqq = _prepare_history(histories.get("QQQ"), "QQQ")
    if qqq is None:
        return _empty_frame()
    spy = _prepare_history(histories.get("SPY"), "SPY")
    if spy is None:
        return _unavailable_frame(qqq.index, "missing_spy_or_qqq")

    common = qqq.index.intersection(spy.index).sort_values()
    if common.empty:
        return _unavailable_frame(qqq.index, "no_common_spy_qqq_sessions")
    qqq = qqq.loc[common]
    spy = spy.loc[common]

    evidence = pd.DataFrame(index=common)
    _add_benchmark_evidence(evidence, "qqq", qqq)
    _add_benchmark_evidence(evidence, "spy", spy)
    qqq_distribution = _distribution_counts(qqq)
    spy_distribution = _distribution_counts(spy)
    evidence["qqq_distribution_days"] = qqq_distribution
    evidence["spy_distribution_days"] = spy_distribution
    evidence["distribution_days"] = np.maximum(
        qqq_distribution,
        spy_distribution,
    )
    breadth = _breadth_frame(histories, common)
    evidence = evidence.join(breadth)

    states = _run_state_machine(
        evidence,
        qqq,
        spy,
        minimum_breadth=minimum_breadth,
    )
    result = evidence.join(states)
    result["gate_version"] = MARKET_GATE_VERSION
    return result.loc[:, MARKET_GATE_COLUMNS]


def latest_market_gate(
    histories: Mapping,
    *,
    asof=None,
    minimum_breadth: int = 20,
) -> dict:
    """Return the latest JSON-ready market gate at or before ``asof``."""
    frame = build_market_gate_frame(
        histories,
        minimum_breadth=minimum_breadth,
    )
    if asof is not None and not frame.empty:
        cutoff = pd.Timestamp(asof)
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        frame = frame.loc[frame.index <= cutoff]
    if frame.empty:
        return _missing_payload("missing_spy_or_qqq")

    row = frame.iloc[-1]
    return {
        "state": row["gate_state"],
        "market_state": row["market_state"],
        "version": MARKET_GATE_VERSION,
        "asof": frame.index[-1].date().isoformat(),
        "point_in_time": True,
        "state_start": _optional_text(row["market_state_start"]),
        "follow_through_date": _optional_text(row["follow_through_date"]),
        "rally_day_count": int(row["rally_day_count"]),
        "values": {
            "distribution_days": int(row["distribution_days"]),
            "qqq_distribution_days": int(row["qqq_distribution_days"]),
            "spy_distribution_days": int(row["spy_distribution_days"]),
            "breadth_above_ema20": _optional_float(
                row["breadth_above_ema20"]
            ),
            "breadth_above_sma50": _optional_float(
                row["breadth_above_sma50"]
            ),
            "breadth_count": int(row["breadth_count"]),
            "qqq_close": _optional_float(row["qqq_close"]),
            "qqq_return_5": _optional_float(row["qqq_return_5"]),
            "qqq_return_20": _optional_float(row["qqq_return_20"]),
        },
        "thresholds": {
            "minimum_common_sessions": MINIMUM_COMMON_SESSIONS,
            "minimum_breadth": minimum_breadth,
            "distribution_window_sessions": DISTRIBUTION_WINDOW_SESSIONS,
            "distribution_removal_gain": DISTRIBUTION_REMOVAL_GAIN,
            "follow_through_minimum_gain": 0.015,
            "follow_through_day_range": [4, 10],
        },
        "reason_codes": list(row["reason_codes"]),
    }


def _run_state_machine(evidence, qqq, spy, *, minimum_breadth):
    result = pd.DataFrame(index=evidence.index)
    states = []
    starts = []
    follow_through_dates = []
    rally_days = []
    reason_rows = []
    state = "range_bound"
    state_start = None
    follow_through_date = None
    rally_day_count = 0
    correction_low = None
    rally_start_position = None

    for position, date in enumerate(evidence.index):
        row = evidence.loc[date]
        available = (
            position + 1 >= MINIMUM_COMMON_SESSIONS
            and int(row["breadth_count"]) >= minimum_breadth
            and _finite_required(row)
        )
        if not available:
            selected_state = "unavailable"
            selected_start = None
            selected_reasons = _unavailable_reasons(
                position,
                row,
                minimum_breadth,
            )
        else:
            previous_state = state
            qqq_close = float(row["qqq_close"])
            qqq_previous = (
                np.nan
                if position == 0
                else float(evidence.iloc[position - 1]["qqq_close"])
            )
            is_up_day = (
                np.isfinite(qqq_previous) and qqq_close > qqq_previous
            )
            acute = row["qqq_return_5"] <= -0.07
            correction_trigger = _correction_trigger(row)
            strong_trend = _strong_trend(row)
            pressure = _pressure_trigger(row)
            recovery = _recovery_trigger(row)

            if state == "market_in_correction":
                correction_low = (
                    qqq_close
                    if correction_low is None
                    else min(correction_low, qqq_close)
                )
                # A rally attempt begins on the first index advance after the
                # correction low; the trailing five-day return may still be
                # deeply negative and must not suppress that first day.
                if is_up_day:
                    state = "rally_attempt"
                    rally_day_count = 1
                    rally_start_position = position
                else:
                    rally_day_count = 0
            elif state == "rally_attempt":
                if (
                    (correction_low is not None and qqq_close < correction_low)
                    or (correction_trigger and not is_up_day)
                ):
                    state = "market_in_correction"
                    correction_low = qqq_close
                    rally_day_count = 0
                    rally_start_position = None
                    follow_through_date = None
                else:
                    rally_day_count += 1
                    if _follow_through_day(
                        evidence,
                        qqq,
                        spy,
                        position,
                        rally_day_count,
                    ):
                        state = "confirmed_uptrend"
                        follow_through_date = date.date().isoformat()
                        rally_start_position = None
                    elif rally_day_count > 10:
                        # The first version intentionally bounds the FTD
                        # window.  A stale rally cannot remain "day 245".
                        state = "market_in_correction"
                        start = (
                            position
                            if rally_start_position is None
                            else rally_start_position
                        )
                        correction_low = float(
                            evidence["qqq_close"].iloc[
                                start : position + 1
                            ].min()
                        )
                        rally_day_count = 0
                        rally_start_position = None
            elif state == "confirmed_uptrend":
                if correction_trigger:
                    state = "market_in_correction"
                    correction_low = qqq_close
                    rally_day_count = 0
                    rally_start_position = None
                    follow_through_date = None
                elif pressure:
                    state = "uptrend_under_pressure"
            elif state == "uptrend_under_pressure":
                if correction_trigger:
                    state = "market_in_correction"
                    correction_low = qqq_close
                    rally_day_count = 0
                    rally_start_position = None
                    follow_through_date = None
                elif recovery:
                    state = "confirmed_uptrend"
            else:
                if correction_trigger:
                    state = "market_in_correction"
                    correction_low = qqq_close
                    rally_day_count = 0
                    rally_start_position = None
                    follow_through_date = None
                elif strong_trend:
                    state = "confirmed_uptrend"

            if state != previous_state or state_start is None:
                state_start = date.date().isoformat()
            selected_state = state
            selected_start = state_start
            selected_reasons = _state_reasons(state, row)

        states.append(selected_state)
        starts.append(selected_start)
        follow_through_dates.append(
            follow_through_date if selected_state != "unavailable" else None
        )
        rally_days.append(
            rally_day_count if selected_state != "unavailable" else 0
        )
        reason_rows.append(tuple(selected_reasons))

    result["gate_state"] = [
        "pass" if value == "confirmed_uptrend" else
        "missing" if value == "unavailable" else "fail"
        for value in states
    ]
    result["market_state"] = states
    result["market_state_start"] = starts
    result["follow_through_date"] = follow_through_dates
    result["rally_day_count"] = rally_days
    result["reason_codes"] = reason_rows
    return result


def _add_benchmark_evidence(target, prefix, history):
    close = history["Close"]
    target[f"{prefix}_close"] = close
    target[f"{prefix}_ema20"] = close.ewm(span=20, adjust=False).mean()
    target[f"{prefix}_sma50"] = close.rolling(50, min_periods=50).mean()
    target[f"{prefix}_sma200"] = close.rolling(
        200,
        min_periods=200,
    ).mean()
    if prefix == "qqq":
        target["qqq_return_5"] = close.pct_change(5, fill_method=None)
        target["qqq_return_20"] = close.pct_change(20, fill_method=None)
        prior_high = close.shift(1).rolling(63, min_periods=63).max()
        target["qqq_drawdown_63"] = close / prior_high - 1.0


def _breadth_frame(histories, common):
    above_ema_columns = {}
    above_sma_columns = {}
    for raw_ticker, source in histories.items():
        ticker = str(raw_ticker).upper()
        if ticker in {"QQQ", "SPY"}:
            continue
        prepared = _prepare_history(source, ticker)
        if prepared is None:
            continue
        close = prepared["Close"].reindex(common)
        above_ema_columns[ticker] = (
            close > close.ewm(span=20, adjust=False).mean()
        ).where(
            close.notna()
        )
        above_sma_columns[ticker] = (
            close > close.rolling(50, min_periods=50).mean()
        ).where(close.notna())
    above_ema = pd.DataFrame(above_ema_columns, index=common)
    above_sma = pd.DataFrame(above_sma_columns, index=common)
    result = pd.DataFrame(index=common)
    if above_ema.empty:
        result["breadth_above_ema20"] = np.nan
        result["breadth_above_sma50"] = np.nan
        result["breadth_count"] = 0
        return result
    valid = above_ema.notna() & above_sma.notna()
    result["breadth_count"] = valid.sum(axis=1).astype(int)
    result["breadth_above_ema20"] = above_ema.where(valid).mean(axis=1)
    result["breadth_above_sma50"] = above_sma.where(valid).mean(axis=1)
    return result


def _distribution_counts(history):
    close = history["Close"].to_numpy(dtype=float)
    volume = history["Volume"].to_numpy(dtype=float)
    active = []
    counts = np.zeros(len(history), dtype=int)
    for position in range(len(history)):
        current_close = close[position]
        active = [
            (event_position, event_close)
            for event_position, event_close in active
            if position - event_position < DISTRIBUTION_WINDOW_SESSIONS
            and current_close < event_close * (1.0 + DISTRIBUTION_REMOVAL_GAIN)
        ]
        if (
            position > 0
            and current_close / close[position - 1] - 1.0 <= -0.002
            and volume[position] > volume[position - 1]
        ):
            active.append((position, current_close))
        counts[position] = len(active)
    return pd.Series(counts, index=history.index, dtype=int)


def _correction_trigger(row):
    return bool(
        row["qqq_return_5"] <= -0.07
        or (
            row["qqq_close"] < row["qqq_sma50"]
            and (
                row["qqq_return_20"] <= -0.05
                or row["qqq_drawdown_63"] <= -0.08
            )
        )
        or (
            row["qqq_close"] < row["qqq_sma50"]
            and row["spy_close"] < row["spy_sma50"]
        )
        or (
            row["distribution_days"] >= 5
            and row["qqq_close"] < row["qqq_ema20"]
        )
    )


def _strong_trend(row):
    return bool(
        row["qqq_close"] > row["qqq_ema20"] > row["qqq_sma50"]
        and row["qqq_close"] > row["qqq_sma200"]
        and row["spy_close"] > row["spy_ema20"]
        and row["spy_close"] > row["spy_sma50"]
        and row["breadth_above_ema20"] >= 0.55
        and row["breadth_above_sma50"] >= 0.50
        and row["qqq_return_20"] > 0.0
    )


def _pressure_trigger(row):
    return bool(
        row["distribution_days"] >= 4
        or sum(
            (
                row["qqq_close"] < row["qqq_ema20"],
                row["spy_close"] < row["spy_ema20"],
                row["breadth_above_ema20"] < 0.50,
            )
        ) >= 2
    )


def _recovery_trigger(row):
    return bool(
        row["qqq_close"] > row["qqq_ema20"]
        and row["qqq_close"] > row["qqq_sma50"]
        and row["spy_close"] > row["spy_ema20"]
        and row["distribution_days"] <= 2
        and row["breadth_above_ema20"] >= 0.55
    )


def _follow_through_day(evidence, qqq, spy, position, rally_day_count):
    if rally_day_count < 4 or rally_day_count > 10 or position == 0:
        return False
    qqq_gain = qqq["Close"].iloc[position] / qqq["Close"].iloc[position - 1] - 1
    spy_gain = spy["Close"].iloc[position] / spy["Close"].iloc[position - 1] - 1
    return bool(
        qqq_gain >= 0.015
        and spy_gain >= 0.015
        and qqq["Volume"].iloc[position] > qqq["Volume"].iloc[position - 1]
        and spy["Volume"].iloc[position] > spy["Volume"].iloc[position - 1]
    )


def _finite_required(row):
    required = (
        "qqq_sma200",
        "spy_sma200",
        "qqq_return_20",
        "qqq_drawdown_63",
        "breadth_above_ema20",
        "breadth_above_sma50",
    )
    return all(np.isfinite(row[column]) for column in required)


def _unavailable_reasons(position, row, minimum_breadth):
    reasons = []
    if position + 1 < MINIMUM_COMMON_SESSIONS:
        reasons.append("insufficient_common_history")
    if int(row["breadth_count"]) < minimum_breadth:
        reasons.append("insufficient_breadth_coverage")
    if not reasons:
        reasons.append("required_market_evidence_unavailable")
    return reasons


def _state_reasons(state, row):
    if state == "confirmed_uptrend":
        return ["confirmed_uptrend_conditions_met"]
    if state == "rally_attempt":
        return ["rally_attempt_waiting_for_follow_through"]
    if state == "market_in_correction":
        reasons = []
        if row["qqq_return_5"] <= -0.07:
            reasons.append("qqq_return_5_le_-7pct")
        if row["qqq_close"] < row["qqq_sma50"]:
            reasons.append("qqq_below_sma50")
        if row["spy_close"] < row["spy_sma50"]:
            reasons.append("spy_below_sma50")
        return reasons or ["market_correction_conditions_persist"]
    if state == "uptrend_under_pressure":
        reasons = []
        if row["distribution_days"] >= 4:
            reasons.append("distribution_days_ge_4")
        if row["qqq_close"] < row["qqq_ema20"]:
            reasons.append("qqq_below_ema20")
        if row["spy_close"] < row["spy_ema20"]:
            reasons.append("spy_below_ema20")
        if row["breadth_above_ema20"] < 0.50:
            reasons.append("breadth_above_ema20_lt_50pct")
        return reasons or ["uptrend_pressure_not_repaired"]
    return ["no_confirmed_market_uptrend"]


def _prepare_history(source, ticker):
    if source is None:
        return None
    if not isinstance(source, pd.DataFrame):
        raise TypeError(f"{ticker} history must be a DataFrame")
    if source.empty:
        return None
    required = ("Open", "High", "Low", "Close", "Volume")
    try:
        result = source.loc[:, required].copy(deep=True)
    except KeyError as exc:
        raise ValueError(f"{ticker} history is missing OHLCV columns") from exc
    if not isinstance(result.index, pd.DatetimeIndex):
        raise TypeError(f"{ticker} history index must be DatetimeIndex")
    if result.index.tz is not None:
        result.index = result.index.tz_localize(None)
    if result.index.has_duplicates:
        raise ValueError(f"{ticker} history contains duplicate dates")
    result = result.sort_index().astype(float)
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError(f"{ticker} history contains non-finite values")
    return result


def _unavailable_frame(index, reason):
    result = pd.DataFrame(index=pd.DatetimeIndex(index))
    result["gate_state"] = "missing"
    result["market_state"] = "unavailable"
    result["market_state_start"] = None
    result["follow_through_date"] = None
    result["rally_day_count"] = 0
    for column in MARKET_GATE_COLUMNS[5:-2]:
        result[column] = 0 if column.endswith("_days") or column == "breadth_count" else np.nan
    result["reason_codes"] = [(reason,)] * len(result)
    result["gate_version"] = MARKET_GATE_VERSION
    return result.loc[:, MARKET_GATE_COLUMNS]


def _empty_frame():
    return _unavailable_frame(pd.DatetimeIndex([]), "missing_spy_or_qqq")


def _missing_payload(reason):
    return {
        "state": "missing",
        "market_state": "unavailable",
        "version": MARKET_GATE_VERSION,
        "asof": None,
        "point_in_time": True,
        "state_start": None,
        "follow_through_date": None,
        "rally_day_count": 0,
        "values": {
            "distribution_days": 0,
            "qqq_distribution_days": 0,
            "spy_distribution_days": 0,
            "breadth_above_ema20": None,
            "breadth_above_sma50": None,
            "breadth_count": 0,
            "qqq_close": None,
            "qqq_return_5": None,
            "qqq_return_20": None,
        },
        "thresholds": {
            "minimum_common_sessions": MINIMUM_COMMON_SESSIONS,
            "minimum_breadth": 20,
            "distribution_window_sessions": DISTRIBUTION_WINDOW_SESSIONS,
            "distribution_removal_gain": DISTRIBUTION_REMOVAL_GAIN,
            "follow_through_minimum_gain": 0.015,
            "follow_through_day_range": [4, 10],
        },
        "reason_codes": [reason],
    }


def _optional_float(value):
    return None if not np.isfinite(value) else float(value)


def _optional_text(value):
    return None if value is None or pd.isna(value) else str(value)
