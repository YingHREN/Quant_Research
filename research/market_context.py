"""Point-in-time market, sector, reversal, and pressure context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from research.market_pressure import Evidence, build_pressure_rows
from research.early_reversal import build_early_reversal_rows
from research.reversal import build_reversal_rows
from research.risk_memory import (
    RISK_MEMORY_ACTIVE_THRESHOLD,
    RISK_MEMORY_HALF_LIFE_SESSIONS,
    RISK_MEMORY_WINDOW_SESSIONS,
    build_risk_memory_state,
)
from web.market_groups import (
    REFERENCE_TICKERS,
    SECTOR_ETFS,
    MarketGroup,
)


SUPPORTED_HORIZONS = (5, 20, 60)
MINIMUM_SCORE_COVERAGE = 0.80

MARKET_RULES_V1 = {
    "qqq_above_ema20": (7.5, True),
    "qqq_above_sma50": (7.5, True),
    "spy_above_ema20": (7.5, True),
    "spy_above_sma50": (7.5, True),
    "breadth_above_ema20": (10.0, 0.60, 0.45),
    "breadth_above_sma50": (10.0, 0.55, 0.40),
    "new_high_low_balance": (5.0, 0.10, 0.00),
    "sector_relative_return_5": (8.0, 0.00, -0.01),
    "sector_relative_return_20": (9.0, 0.00, -0.02),
    "sector_relative_return_60": (8.0, 0.00, -0.04),
    "distribution_count_20_safe": (12.0, 2, 4),
    "atr20_ratio_safe": (8.0, 1.10, 1.25),
}
OPPORTUNITY_RULES_V1 = {
    "qqq_not_new_20_low": (7.0, True),
    "qqq_cross_above_ema20": (7.0, True),
    "qqq_downside_range_contracting": (6.0, True),
    "sector_relative_return_5_positive": (10.0, 0.00, -0.005),
    "sector_relative_slope_20_positive": (10.0, 0.00, -0.001),
    "higher_low_confirmed": (12.0, True),
    "trendline_breakout": (12.0, True),
    "prior_high_breakout": (11.0, True),
    "capitulation_recovery": (10.0, True),
    "signed_volume_proxy_positive": (7.5, 0.50, 0.00),
    "up_volume_confirmation": (7.5, True),
}
RISK_RULES_V1 = {
    "qqq_cross_below_ema20": (10.0, True),
    "qqq_distribution_count_20": (10.0, 4, 2),
    "sector_relative_return_5_negative": (10.0, -0.01, 0.00),
    "sector_relative_slope_20_negative": (10.0, -0.001, 0.00),
    "failed_breakout": (12.0, True),
    "cross_below_ema20": (8.0, True),
    "cross_below_sma50": (8.0, True),
    "stock_sector_rs_breakdown": (7.0, True),
    "distribution_day": (10.0, True),
    "high_volume_non_progress": (6.0, True),
    "upper_wick_supply": (5.0, 0.45, 0.30),
    "signed_volume_proxy_negative": (4.0, -0.50, 0.00),
}

_PRESSURE_ATOMIC_COLUMNS = (
    "close_location",
    "upper_wick_ratio",
    "signed_volume_proxy",
    "distribution_day",
    "failed_breakout",
)
_REVERSAL_ATOMIC_COLUMNS = (
    "prior_high_breakout",
    "trendline_breakout",
    "higher_low_confirmed",
)
_CROSS_MARKET_ATOMIC_COLUMNS = (
    "qqq_trend_state",
    "qqq_close_vs_ema20_pct",
    "qqq_return_5",
    "qqq_return_20",
    "qqq_volume_ratio",
    "sector_trend_state",
    "sector_relative_strength_20",
    "stock_sector_relative_strength_20",
)
_EARLY_REVERSAL_ATOMIC_COLUMNS = (
    "early_prior_session_selloff",
    "early_current_price_acceptance",
    "early_descending_trendline_proximity",
    "early_current_volume_support",
)


@dataclass(frozen=True)
class CompositeScore:
    score: float | None
    coverage: float
    evidence: tuple[Evidence, ...]
    unavailable_reason: str | None = None

    def to_dict(self):
        return {
            "score": self.score,
            "coverage": self.coverage,
            "unavailable_reason": self.unavailable_reason,
            "evidence": [_evidence_dict(item) for item in self.evidence],
        }


@dataclass(frozen=True)
class _Prepared:
    history: pd.DataFrame
    pressure: pd.DataFrame
    reversal: pd.DataFrame
    ema20: pd.Series
    sma50: pd.Series
    atr20: pd.Series


def score_evidence(
    evidence: Iterable[Evidence],
    *,
    required_available: bool,
    unavailable_reason: str,
) -> CompositeScore:
    rows = tuple(evidence)
    maximum = sum(float(item.max_points) for item in rows)
    available = sum(
        float(item.max_points)
        for item in rows
        if item.state != "unavailable"
    )
    coverage = 0.0 if maximum == 0.0 else available / maximum
    if not required_available or coverage < MINIMUM_SCORE_COVERAGE:
        return CompositeScore(None, coverage, rows, unavailable_reason)
    points = sum(
        float(item.points)
        for item in rows
        if item.state != "unavailable"
    )
    return CompositeScore(
        round(points / available * 100.0, 2),
        coverage,
        rows,
    )


def build_market_context(histories, asof, group: MarketGroup, horizon):
    """Build one JSON-ready, point-in-time market overview."""
    checked_horizon = _horizon(horizon)
    cutoff = _cutoff(asof)
    prepared = _prepare_histories(histories, cutoff)
    sector = _sector_composite(prepared, group)
    available_benchmarks = [
        ticker for ticker in group.benchmark_tickers if ticker in prepared
    ]
    source_tickers = _available_sector_sources(prepared, group)
    benchmark_coverage = (
        len(available_benchmarks) / len(group.benchmark_tickers)
        if group.benchmark_tickers
        else 0.0
    )
    common_asof = _common_asof(prepared, cutoff)
    market_score = _market_score(prepared, sector, common_asof)
    constituents = _constituent_payloads(
        prepared,
        group,
        sector,
        common_asof,
    )
    opportunity_scores = [
        row["reversal_opportunity"]["score"]
        for row in constituents
        if row["reversal_opportunity"]["score"] is not None
    ]
    risk_scores = [
        row["downside_risk"]["score"]
        for row in constituents
        if row["downside_risk"]["score"] is not None
    ]
    risk_state_scores = [
        row["downside_risk"]["state_score"]
        for row in constituents
        if row["downside_risk"]["state_score"] is not None
    ]
    selected_returns = {
        str(window): _return_at(sector, common_asof, window)
        for window in (1, 5, 20, 60)
    }
    group_raw_risk = _aggregate_score(risk_scores, benchmark_coverage)
    aggregated_state_risk = _aggregate_score(
        risk_state_scores,
        benchmark_coverage,
    )
    group_risk = dict(group_raw_risk)
    group_risk.update(
        {
            "raw_score": group_raw_risk["score"],
            "state_score": aggregated_state_risk["score"],
            "state": _aggregate_risk_state(
                constituents,
                aggregated_state_risk["score"],
                group_raw_risk["score"],
            ),
            "memory_half_life_sessions": (
                RISK_MEMORY_HALF_LIFE_SESSIONS
            ),
            "memory_window_sessions": RISK_MEMORY_WINDOW_SESSIONS,
            "model_key": "bearish_turn_risk_rules_v2",
        }
    )
    selected_group = {
        "key": group.key,
        "label_key": group.label_key,
        "benchmark_tickers": list(group.benchmark_tickers),
        "available_benchmarks": available_benchmarks,
        "source_tickers": list(source_tickers),
        "coverage": benchmark_coverage,
        "latest_source_date": _iso(common_asof),
        "returns": selected_returns,
        "reversal_opportunity": _aggregate_score(
            opportunity_scores,
            benchmark_coverage,
        ),
        "downside_risk": group_risk,
    }
    return {
        "asof": _iso(common_asof),
        "requested_horizon": checked_horizon,
        "selected_sector": group.key,
        "evidence_tier": "daily_proxy",
        "intraday": {
            "state": "unavailable",
            "reason": "intraday_not_integrated",
        },
        "market_posture": market_score.to_dict(),
        "sectors": _sector_rows(prepared, common_asof, checked_horizon),
        "selected_group": selected_group,
        "constituents": constituents,
        "changed_events": _changed_events(prepared, group, common_asof),
    }


def build_atomic_model_rows(histories, group: MarketGroup) -> pd.DataFrame:
    """Return causal atomic rows without UI composite scores."""
    prepared = _prepare_histories(histories, None)
    group_tickers = frozenset(
        (*group.constituent_tickers, *group.related_tickers)
    )
    qqq_trend = _qqq_trend_series(prepared.get("QQQ"))
    qqq_continuous = _qqq_continuous_frame(prepared.get("QQQ"))
    sector = _sector_composite(prepared, group)
    sector_trend = _price_trend_series(sector)
    sector_relative = _relative_strength_series(
        sector,
        (
            None
            if "QQQ" not in prepared
            else prepared["QQQ"].history["Close"].astype(float)
        ),
        20,
    )
    frames = {}
    for ticker, item in prepared.items():
        frame = pd.DataFrame(index=item.history.index)
        for column in _PRESSURE_ATOMIC_COLUMNS:
            frame[f"pressure_{column}"] = item.pressure[column].astype(float)
        for column in _REVERSAL_ATOMIC_COLUMNS:
            frame[column] = item.reversal[column].astype(float)
        early = pd.DataFrame(
            build_early_reversal_rows(
                item.history,
                item.reversal.to_dict(orient="records"),
            ),
            index=item.history.index,
        )
        for column in _EARLY_REVERSAL_ATOMIC_COLUMNS:
            frame[column] = early[column].astype(float)
        frame["qqq_trend_state"] = _causal_reindex(
            qqq_trend,
            frame.index,
        )
        for column in qqq_continuous:
            frame[column] = _causal_reindex(
                qqq_continuous[column],
                frame.index,
            )
        if ticker in group_tickers:
            frame["sector_trend_state"] = _causal_reindex(
                sector_trend,
                frame.index,
            )
            frame["sector_relative_strength_20"] = _causal_reindex(
                sector_relative,
                frame.index,
            )
            stock_relative = _relative_strength_series(
                item.history["Close"].astype(float),
                sector,
                20,
            )
            frame["stock_sector_relative_strength_20"] = _causal_reindex(
                stock_relative,
                frame.index,
            )
        else:
            frame["sector_trend_state"] = np.nan
            frame["sector_relative_strength_20"] = np.nan
            frame["stock_sector_relative_strength_20"] = np.nan
        frames[ticker] = frame
    if not frames:
        return _empty_multiindex_frame(
            tuple(
                f"pressure_{column}" for column in _PRESSURE_ATOMIC_COLUMNS
            )
            + _REVERSAL_ATOMIC_COLUMNS
            + _CROSS_MARKET_ATOMIC_COLUMNS
            + _EARLY_REVERSAL_ATOMIC_COLUMNS
        )
    return pd.concat(
        frames,
        names=("ticker", "observation_date"),
    ).sort_index()


def _qqq_trend_series(item):
    if item is None:
        return None
    close = item.history["Close"].astype(float)
    valid = item.ema20.notna() & item.sma50.notna()
    values = np.select(
        (
            valid & (close > item.ema20) & (item.ema20 > item.sma50),
            valid & (close < item.ema20) & (item.ema20 < item.sma50),
        ),
        (1.0, -1.0),
        default=0.0,
    )
    return pd.Series(values, index=close.index, dtype=float).where(valid)


def _price_trend_series(close):
    if close is None or close.empty:
        return None
    close = close.sort_index().astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    valid = ema20.notna() & sma50.notna()
    values = np.select(
        (
            valid & (close > ema20) & (ema20 > sma50),
            valid & (close < ema20) & (ema20 < sma50),
        ),
        (1.0, -1.0),
        default=0.0,
    )
    return pd.Series(values, index=close.index, dtype=float).where(valid)


def _qqq_continuous_frame(item):
    columns = (
        "qqq_close_vs_ema20_pct",
        "qqq_return_5",
        "qqq_return_20",
        "qqq_volume_ratio",
    )
    if item is None:
        return pd.DataFrame(columns=columns, dtype=float)
    close = item.history["Close"].astype(float)
    result = pd.DataFrame(index=close.index)
    result["qqq_close_vs_ema20_pct"] = (
        close / item.ema20.replace(0.0, np.nan) - 1.0
    ) * 100.0
    result["qqq_return_5"] = close.pct_change(5, fill_method=None)
    result["qqq_return_20"] = close.pct_change(20, fill_method=None)
    result["qqq_volume_ratio"] = item.pressure["volume_ratio"].astype(float)
    return result.loc[:, columns].where(np.isfinite(result), np.nan)


def _relative_strength_series(first, second, window):
    if first is None or second is None:
        return None
    first = first.sort_index().astype(float)
    second_asof = second.sort_index().astype(float).reindex(
        first.index,
        method="ffill",
    )
    first_return = first / first.shift(window) - 1.0
    second_return = second_asof / second_asof.shift(window) - 1.0
    result = first_return - second_return
    return result.where(np.isfinite(result), np.nan)


def _causal_reindex(series, index):
    if series is None or series.empty:
        return pd.Series(np.nan, index=index, dtype=float)
    return series.sort_index().reindex(index, method="ffill").astype(float)


def build_group_score_frame(histories, group: MarketGroup) -> pd.DataFrame:
    """Return causal per-stock score history for later matured calibration."""
    prepared = _prepare_histories(histories, None)
    sector = _sector_composite(prepared, group)
    tickers = tuple(
        dict.fromkeys((*group.constituent_tickers, *group.related_tickers))
    )
    sector_available = sector is not None and not sector.empty
    qqq_available = "QQQ" in prepared
    common = _historical_common_evidence(prepared.get("QQQ"), sector)
    frames = {}
    for ticker in tickers:
        item = prepared.get(ticker)
        if item is None:
            continue
        frames[ticker] = _historical_stock_score_frame(
            item,
            sector,
            common,
            required_available=sector_available and qqq_available,
        )
    if not frames:
        return _empty_multiindex_frame(
            (
                "reversal_opportunity_score",
                "reversal_opportunity_coverage",
                "downside_risk_score",
                "downside_risk_coverage",
                "downside_risk_state_score",
                "downside_risk_state",
                "downside_risk_memory_age_sessions",
                "atr20_pct",
            )
        )
    return pd.concat(
        frames,
        names=("ticker", "observation_date"),
    ).sort_index()


def _historical_common_evidence(qqq, sector):
    if qqq is None:
        return {
            key: None
            for key in (
                "qqq_not_new_20_low",
                "qqq_cross_above_ema20",
                "qqq_cross_below_ema20",
                "qqq_downside_range_contracting",
                "qqq_distribution_count_20",
                "sector_relative_return_5",
                "sector_relative_slope_20",
            )
        }
    close = qqq.history["Close"].astype(float)
    prior_low = close.shift(1).rolling(20, min_periods=20).min()
    not_low = (close > prior_low).where(prior_low.notna())
    cross_up = (
        (close.shift(1) <= qqq.ema20.shift(1))
        & (close > qqq.ema20)
    ).where(qqq.ema20.shift(1).notna() & qqq.ema20.notna())
    cross_down = (
        (close.shift(1) >= qqq.ema20.shift(1))
        & (close < qqq.ema20)
    ).where(qqq.ema20.shift(1).notna() & qqq.ema20.notna())
    previous = close.shift(1)
    downside = pd.concat(
        (
            qqq.history["High"].astype(float)
            - qqq.history["Low"].astype(float),
            (qqq.history["Low"].astype(float) - previous).abs(),
        ),
        axis=1,
    ).max(axis=1)
    recent = downside.rolling(5, min_periods=5).mean()
    preceding = downside.shift(5).rolling(5, min_periods=5).mean()
    contracting = (recent < preceding).where(
        recent.notna() & preceding.notna()
    )
    distribution = (
        qqq.pressure["distribution_day"]
        .astype(float)
        .rolling(20, min_periods=20)
        .sum()
    )
    relative_5 = _relative_strength_series(sector, close, 5)
    relative_slope = _relative_slope_series(sector, close, 20)
    return {
        "qqq_not_new_20_low": not_low,
        "qqq_cross_above_ema20": cross_up,
        "qqq_cross_below_ema20": cross_down,
        "qqq_downside_range_contracting": contracting,
        "qqq_distribution_count_20": distribution,
        "sector_relative_return_5": relative_5,
        "sector_relative_slope_20": relative_slope,
    }


def _historical_stock_score_frame(
    item,
    sector,
    common,
    *,
    required_available,
):
    index = item.history.index
    aligned = {
        key: _causal_reindex(series, index)
        for key, series in common.items()
    }
    pressure = item.pressure
    reversal = item.reversal
    daily_return = (
        item.history["Close"].astype(float)
        / item.history["Close"].astype(float).shift(1)
        - 1.0
    )
    up_volume = (
        (daily_return > 0.0)
        & (pressure["volume_ratio"] >= 1.2)
        & (pressure["close_location"] >= 0.4)
    ).where(
        daily_return.notna()
        & pressure["volume_ratio"].notna()
        & pressure["close_location"].notna()
    )
    opportunity_rules = (
        _boolean_rule(aligned["qqq_not_new_20_low"], 7.0),
        _boolean_rule(aligned["qqq_cross_above_ema20"], 7.0),
        _boolean_rule(aligned["qqq_downside_range_contracting"], 6.0),
        _numeric_rule(
            aligned["sector_relative_return_5"],
            10.0,
            0.00,
            -0.005,
        ),
        _numeric_rule(
            aligned["sector_relative_slope_20"],
            10.0,
            0.00,
            -0.001,
        ),
        _boolean_rule(reversal["higher_low_confirmed"], 12.0),
        _boolean_rule(reversal["trendline_breakout"], 12.0),
        _boolean_rule(reversal["prior_high_breakout"], 11.0),
        _boolean_rule(pressure["capitulation_recovery"], 10.0),
        _numeric_rule(
            pressure["signed_volume_proxy"],
            7.5,
            0.50,
            0.00,
        ),
        _boolean_rule(up_volume, 7.5),
    )
    cross_below_ema = _cross_series(
        item.history["Close"],
        item.ema20,
        direction="down",
    )
    cross_below_sma = _cross_series(
        item.history["Close"],
        item.sma50,
        direction="down",
    )
    rs_breakdown = _relative_breakdown_series(
        item.history["Close"],
        sector,
        20,
    )
    upper_supply = pressure["upper_wick_ratio"].where(
        pressure["volume_ratio"] >= 1.2,
        0.0,
    ).where(
        pressure["upper_wick_ratio"].notna()
        & pressure["volume_ratio"].notna()
    )
    risk_rules = (
        _boolean_rule(aligned["qqq_cross_below_ema20"], 10.0),
        _numeric_rule(
            aligned["qqq_distribution_count_20"],
            10.0,
            4.0,
            2.0,
        ),
        _numeric_rule(
            aligned["sector_relative_return_5"],
            10.0,
            -0.01,
            0.00,
            direction="low",
        ),
        _numeric_rule(
            aligned["sector_relative_slope_20"],
            10.0,
            -0.001,
            0.00,
            direction="low",
        ),
        _boolean_rule(pressure["failed_breakout"], 12.0),
        _boolean_rule(cross_below_ema, 8.0),
        _boolean_rule(cross_below_sma, 8.0),
        _boolean_rule(rs_breakdown, 7.0),
        _boolean_rule(pressure["distribution_day"], 10.0),
        _boolean_rule(pressure["high_volume_non_progress"], 6.0),
        _numeric_rule(upper_supply, 5.0, 0.45, 0.30),
        _numeric_rule(
            pressure["signed_volume_proxy"],
            4.0,
            -0.50,
            0.00,
            direction="low",
        ),
    )
    opportunity, opportunity_coverage = _score_rule_series(
        opportunity_rules,
        index,
        required_available,
    )
    risk, risk_coverage = _score_rule_series(
        risk_rules,
        index,
        required_available,
    )
    close = item.history["Close"].replace(0.0, np.nan).astype(float)
    result = pd.DataFrame(
        {
            "reversal_opportunity_score": opportunity,
            "reversal_opportunity_coverage": opportunity_coverage,
            "downside_risk_score": risk,
            "downside_risk_coverage": risk_coverage,
            "atr20_pct": item.atr20 / close * 100.0,
        },
        index=index,
    )
    memory = build_risk_memory_state(result["downside_risk_score"])
    result["downside_risk_state_score"] = memory["state_score"]
    result["downside_risk_state"] = memory["state"]
    result["downside_risk_memory_age_sessions"] = memory[
        "memory_age_sessions"
    ]
    return result


def _boolean_rule(values, weight):
    numeric = pd.to_numeric(values, errors="coerce")
    available = numeric.notna().astype(float) * weight
    points = (numeric.astype(float) != 0.0).astype(float) * weight
    return points.where(numeric.notna(), 0.0), available


def _numeric_rule(
    values,
    weight,
    met_threshold,
    near_threshold,
    *,
    direction="high",
):
    numeric = pd.to_numeric(values, errors="coerce")
    available_mask = numeric.notna() & np.isfinite(numeric)
    if direction == "high":
        met = numeric >= met_threshold
        near = (numeric < met_threshold) & (numeric >= near_threshold)
    else:
        met = numeric <= met_threshold
        near = (numeric > met_threshold) & (numeric <= near_threshold)
    points = pd.Series(0.0, index=numeric.index)
    points.loc[near & available_mask] = weight / 2.0
    points.loc[met & available_mask] = weight
    available = available_mask.astype(float) * weight
    return points, available


def _score_rule_series(rules, index, required_available):
    points = pd.Series(0.0, index=index)
    available = pd.Series(0.0, index=index)
    for rule_points, rule_available in rules:
        points = points.add(rule_points.reindex(index), fill_value=0.0)
        available = available.add(
            rule_available.reindex(index),
            fill_value=0.0,
        )
    coverage = available / 100.0
    score = points / available.replace(0.0, np.nan) * 100.0
    score = score.where(
        bool(required_available) & (coverage >= MINIMUM_SCORE_COVERAGE)
    )
    return score.round(2), coverage


def _cross_series(close, average, *, direction):
    valid = (
        close.shift(1).notna()
        & average.shift(1).notna()
        & close.notna()
        & average.notna()
    )
    if direction == "up":
        crossed = (close.shift(1) <= average.shift(1)) & (close > average)
    else:
        crossed = (close.shift(1) >= average.shift(1)) & (close < average)
    return crossed.where(valid)


def _relative_slope_series(first, second, window):
    if first is None or second is None:
        return None
    first = first.sort_index().astype(float)
    second_asof = second.sort_index().astype(float).reindex(
        first.index,
        method="ffill",
    )
    ratio = first / second_asof.replace(0.0, np.nan)
    log_ratio = np.log(ratio.where(ratio > 0.0))
    x = np.arange(window, dtype=float)
    return log_ratio.rolling(window, min_periods=window).apply(
        lambda values: float(np.polyfit(x, values, 1)[0]),
        raw=True,
    )


def _relative_breakdown_series(close, sector, window):
    if sector is None:
        return pd.Series(np.nan, index=close.index, dtype=float)
    sector_asof = sector.sort_index().reindex(close.index, method="ffill")
    ratio = close.astype(float) / sector_asof.replace(0.0, np.nan)
    average = ratio.rolling(window, min_periods=window).mean()
    valid = (
        ratio.shift(1).notna()
        & average.shift(1).notna()
        & ratio.notna()
        & average.notna()
    )
    return (
        (ratio.shift(1) >= average.shift(1)) & (ratio < average)
    ).where(valid)


def _prepare_histories(histories, cutoff) -> dict[str, _Prepared]:
    result = {}
    for raw_ticker, source in dict(histories).items():
        ticker = str(raw_ticker).strip().upper()
        if not ticker or not isinstance(source, pd.DataFrame) or source.empty:
            continue
        history = source.copy(deep=True).sort_index()
        if not isinstance(history.index, pd.DatetimeIndex):
            continue
        history.index = pd.DatetimeIndex(history.index).tz_localize(None)
        if cutoff is not None:
            history = history.loc[history.index <= cutoff]
        if history.empty:
            continue
        try:
            pressure = build_pressure_rows(history)
        except (TypeError, ValueError):
            continue
        reversal = pd.DataFrame(
            build_reversal_rows(history),
            index=history.index,
        )
        close = history["Close"].astype(float)
        previous = close.shift(1)
        true_range = pd.concat(
            (
                history["High"].astype(float) - history["Low"].astype(float),
                (history["High"].astype(float) - previous).abs(),
                (history["Low"].astype(float) - previous).abs(),
            ),
            axis=1,
        ).max(axis=1)
        result[ticker] = _Prepared(
            history=history,
            pressure=pressure,
            reversal=reversal,
            ema20=close.ewm(span=20, adjust=False).mean(),
            sma50=close.rolling(50, min_periods=50).mean(),
            atr20=true_range.rolling(20, min_periods=20).mean(),
        )
    return result


def _sector_composite(prepared, group):
    returns = {}
    for ticker in _available_sector_sources(prepared, group):
        item = prepared.get(ticker)
        if item is None:
            continue
        close = item.history["Close"].astype(float)
        returns[ticker] = close / close.shift(1) - 1.0
    if not returns:
        return None
    mean_return = pd.concat(returns, axis=1).mean(axis=1, skipna=True)
    return (1.0 + mean_return.fillna(0.0)).cumprod()


def _available_sector_sources(prepared, group):
    primary = tuple(
        ticker for ticker in group.benchmark_tickers if ticker in prepared
    )
    if primary:
        return primary
    return tuple(
        ticker
        for ticker in group.fallback_benchmark_tickers
        if ticker in prepared
    )


def _market_score(prepared, sector, asof):
    qqq = prepared.get("QQQ")
    spy = prepared.get("SPY")
    sector_available = sector is not None and not sector.empty
    required = qqq is not None and spy is not None and sector_available
    relative = {
        window: _relative_return(sector, qqq, asof, window)
        for window in (5, 20, 60)
    }
    breadth20, breadth50, high_low = _breadth(prepared, asof)
    qqq_distribution = (
        None
        if qqq is None
        else _tail_sum(qqq.pressure["distribution_day"], asof, 20)
    )
    atr_ratio = _atr_ratio(qqq, asof)
    evidence = (
        _bool_evidence(
            "qqq_above_ema20",
            _above_average(qqq, asof, "ema20"),
            7.5,
            "20 sessions",
        ),
        _bool_evidence(
            "qqq_above_sma50",
            _above_average(qqq, asof, "sma50"),
            7.5,
            "50 sessions",
        ),
        _bool_evidence(
            "spy_above_ema20",
            _above_average(spy, asof, "ema20"),
            7.5,
            "20 sessions",
        ),
        _bool_evidence(
            "spy_above_sma50",
            _above_average(spy, asof, "sma50"),
            7.5,
            "50 sessions",
        ),
        _numeric_evidence(
            "breadth_above_ema20",
            breadth20,
            10.0,
            0.60,
            0.45,
            "1 session",
        ),
        _numeric_evidence(
            "breadth_above_sma50",
            breadth50,
            10.0,
            0.55,
            0.40,
            "1 session",
        ),
        _numeric_evidence(
            "new_high_low_balance",
            high_low,
            5.0,
            0.10,
            0.00,
            "20 sessions",
        ),
        _numeric_evidence(
            "sector_relative_return_5",
            relative[5],
            8.0,
            0.00,
            -0.01,
            "5 sessions",
        ),
        _numeric_evidence(
            "sector_relative_return_20",
            relative[20],
            9.0,
            0.00,
            -0.02,
            "20 sessions",
        ),
        _numeric_evidence(
            "sector_relative_return_60",
            relative[60],
            8.0,
            0.00,
            -0.04,
            "60 sessions",
        ),
        _numeric_evidence(
            "distribution_count_20_safe",
            qqq_distribution,
            12.0,
            2.0,
            4.0,
            "20 sessions",
            direction="low",
        ),
        _numeric_evidence(
            "atr20_ratio_safe",
            atr_ratio,
            8.0,
            1.10,
            1.25,
            "63 sessions",
            direction="low",
        ),
    )
    reason = (
        "missing_market_benchmark"
        if qqq is None or spy is None
        else "missing_sector_benchmark"
        if not sector_available
        else "insufficient_coverage"
    )
    return score_evidence(
        evidence,
        required_available=required,
        unavailable_reason=reason,
    )


def _constituent_payloads(prepared, group, sector, asof):
    rows = []
    required_available = (
        sector is not None and not sector.empty and "QQQ" in prepared
    )
    common = _historical_common_evidence(prepared.get("QQQ"), sector)
    for ticker in (*group.constituent_tickers, *group.related_tickers):
        item = prepared.get(ticker)
        if item is None:
            continue
        observation_date = min(pd.Timestamp(asof), item.history.index[-1])
        opportunity, risk = _stock_scores(
            ticker,
            observation_date,
            prepared,
            sector,
            required_available=required_available,
        )
        historical_scores = _historical_stock_score_frame(
            item,
            sector,
            common,
            required_available=required_available,
        )
        memory_row = historical_scores.loc[:observation_date].iloc[-1]
        pressure = item.pressure.loc[:observation_date].iloc[-1]
        relative_strength = _stock_sector_relative_return(
            item.history["Close"],
            sector,
            observation_date,
            20,
        )
        signed = _finite_or_none(pressure["signed_volume_proxy"])
        risk_payload = risk.to_dict()
        risk_payload.update(
            {
                "raw_score": risk.score,
                "state_score": _finite_or_none(
                    memory_row["downside_risk_state_score"]
                ),
                "state": str(memory_row["downside_risk_state"]),
                "memory_age_sessions": _integer_or_none(
                    memory_row["downside_risk_memory_age_sessions"]
                ),
                "memory_half_life_sessions": (
                    RISK_MEMORY_HALF_LIFE_SESSIONS
                ),
                "memory_window_sessions": RISK_MEMORY_WINDOW_SESSIONS,
                "model_key": "bearish_turn_risk_rules_v2",
            }
        )
        rows.append(
            {
                "ticker": ticker,
                "classification": (
                    f"{group.key}_constituent"
                    if ticker in group.constituent_tickers
                    else f"{group.key}_related"
                ),
                "observation_date": _iso(observation_date),
                "relative_strength_20": relative_strength,
                "pressure_state": (
                    "unavailable"
                    if signed is None
                    else "buying"
                    if signed >= 0.5
                    else "selling"
                    if signed <= -0.5
                    else "balanced"
                ),
                "reversal_opportunity": opportunity.to_dict(),
                "downside_risk": risk_payload,
            }
        )
    return rows


def _stock_scores(
    ticker,
    asof,
    prepared,
    sector,
    *,
    required_available,
):
    item = prepared[ticker]
    qqq = prepared.get("QQQ")
    qqq_cross_up = _cross(qqq, asof, "ema20", direction="up")
    qqq_cross_down = _cross(qqq, asof, "ema20", direction="down")
    qqq_not_low = _not_new_low(qqq, asof, 20)
    contracting = _downside_range_contracting(qqq, asof)
    qqq_distribution = (
        None
        if qqq is None
        else _tail_sum(qqq.pressure["distribution_day"], asof, 20)
    )
    sector_relative_5 = _relative_return(sector, qqq, asof, 5)
    sector_slope = _relative_slope(sector, qqq, asof, 20)
    pressure = _row_at(item.pressure, asof)
    reversal = _row_at(item.reversal, asof)
    signed = (
        None
        if pressure is None
        else _finite_or_none(pressure["signed_volume_proxy"])
    )
    volume_ratio = (
        None
        if pressure is None
        else _finite_or_none(pressure["volume_ratio"])
    )
    close_location = (
        None
        if pressure is None
        else _finite_or_none(pressure["close_location"])
    )
    daily_return = _daily_return(item.history["Close"], asof)
    up_volume = (
        None
        if daily_return is None
        or volume_ratio is None
        or close_location is None
        else daily_return > 0.0
        and volume_ratio >= 1.2
        and close_location >= 0.4
    )
    opportunity_evidence = (
        _bool_evidence(
            "qqq_not_new_20_low",
            qqq_not_low,
            7.0,
            "20 sessions",
        ),
        _bool_evidence(
            "qqq_cross_above_ema20",
            qqq_cross_up,
            7.0,
            "2 sessions",
        ),
        _bool_evidence(
            "qqq_downside_range_contracting",
            contracting,
            6.0,
            "10 sessions",
        ),
        _numeric_evidence(
            "sector_relative_return_5_positive",
            sector_relative_5,
            10.0,
            0.00,
            -0.005,
            "5 sessions",
        ),
        _numeric_evidence(
            "sector_relative_slope_20_positive",
            sector_slope,
            10.0,
            0.00,
            -0.001,
            "20 sessions",
        ),
        _bool_evidence(
            "higher_low_confirmed",
            _row_bool(reversal, "higher_low_confirmed"),
            12.0,
            "causal swing",
        ),
        _bool_evidence(
            "trendline_breakout",
            _row_bool(reversal, "trendline_breakout"),
            12.0,
            "causal swing",
        ),
        _bool_evidence(
            "prior_high_breakout",
            _row_bool(reversal, "prior_high_breakout"),
            11.0,
            "20 sessions",
        ),
        _bool_evidence(
            "capitulation_recovery",
            _row_bool(pressure, "capitulation_recovery"),
            10.0,
            "2 sessions",
        ),
        _numeric_evidence(
            "signed_volume_proxy_positive",
            signed,
            7.5,
            0.50,
            0.00,
            "1 session",
        ),
        _bool_evidence(
            "up_volume_confirmation",
            up_volume,
            7.5,
            "1 session",
        ),
    )
    cross_below_ema = _cross(item, asof, "ema20", direction="down")
    cross_below_sma = _cross(item, asof, "sma50", direction="down")
    rs_breakdown = _stock_sector_breakdown(
        item.history["Close"],
        sector,
        asof,
    )
    upper_wick = (
        None
        if pressure is None
        else _finite_or_none(pressure["upper_wick_ratio"])
    )
    upper_supply = (
        None
        if upper_wick is None or volume_ratio is None
        else upper_wick if volume_ratio >= 1.2 else 0.0
    )
    risk_evidence = (
        _bool_evidence(
            "qqq_cross_below_ema20",
            qqq_cross_down,
            10.0,
            "2 sessions",
        ),
        _numeric_evidence(
            "qqq_distribution_count_20",
            qqq_distribution,
            10.0,
            4.0,
            2.0,
            "20 sessions",
        ),
        _numeric_evidence(
            "sector_relative_return_5_negative",
            sector_relative_5,
            10.0,
            -0.01,
            0.00,
            "5 sessions",
            direction="low",
        ),
        _numeric_evidence(
            "sector_relative_slope_20_negative",
            sector_slope,
            10.0,
            -0.001,
            0.00,
            "20 sessions",
            direction="low",
        ),
        _bool_evidence(
            "failed_breakout",
            _row_bool(pressure, "failed_breakout"),
            12.0,
            "20 sessions",
        ),
        _bool_evidence(
            "cross_below_ema20",
            cross_below_ema,
            8.0,
            "2 sessions",
        ),
        _bool_evidence(
            "cross_below_sma50",
            cross_below_sma,
            8.0,
            "2 sessions",
        ),
        _bool_evidence(
            "stock_sector_rs_breakdown",
            rs_breakdown,
            7.0,
            "20 sessions",
        ),
        _bool_evidence(
            "distribution_day",
            _row_bool(pressure, "distribution_day"),
            10.0,
            "1 session",
        ),
        _bool_evidence(
            "high_volume_non_progress",
            _row_bool(pressure, "high_volume_non_progress"),
            6.0,
            "1 session",
        ),
        _numeric_evidence(
            "upper_wick_supply",
            upper_supply,
            5.0,
            0.45,
            0.30,
            "1 session",
        ),
        _numeric_evidence(
            "signed_volume_proxy_negative",
            signed,
            4.0,
            -0.50,
            0.00,
            "1 session",
            direction="low",
        ),
    )
    reason = (
        "missing_sector_benchmark"
        if sector is None or sector.empty
        else "missing_market_benchmark"
        if qqq is None
        else "insufficient_coverage"
    )
    return (
        score_evidence(
            opportunity_evidence,
            required_available=required_available,
            unavailable_reason=reason,
        ),
        score_evidence(
            risk_evidence,
            required_available=required_available,
            unavailable_reason=reason,
        ),
    )


def _sector_rows(prepared, asof, horizon):
    rows = []
    qqq = prepared.get("QQQ")
    for key, ticker in SECTOR_ETFS.items():
        item = prepared.get(ticker)
        if item is None:
            continue
        sector_return = _return_at(item.history["Close"], asof, horizon)
        qqq_return = (
            None
            if qqq is None
            else _return_at(qqq.history["Close"], asof, horizon)
        )
        relative = (
            None
            if sector_return is None or qqq_return is None
            else sector_return - qqq_return
        )
        risk_flags = _row_at(item.pressure, asof)
        risk_score = None
        if risk_flags is not None:
            risk_score = round(
                100.0
                * (
                    float(bool(risk_flags["distribution_day"]))
                    + float(bool(risk_flags["failed_breakout"]))
                    + float(bool(risk_flags["high_volume_non_progress"]))
                )
                / 3.0,
                2,
            )
        rows.append(
            {
                "key": key,
                "label_key": f"market.sector.{key}",
                "ticker": ticker,
                "return": sector_return,
                "relative_return": relative,
                "downside_risk": {
                    "score": risk_score,
                    "coverage": 1.0,
                    "unavailable_reason": (
                        None if risk_score is not None else "insufficient_history"
                    ),
                },
            }
        )
    return rows


def _changed_events(prepared, group, asof):
    result = []
    keys = (
        "distribution_day",
        "high_volume_non_progress",
        "failed_breakout",
        "capitulation_recovery",
    )
    for ticker in (*group.constituent_tickers, *group.related_tickers):
        item = prepared.get(ticker)
        if item is None:
            continue
        frame = item.pressure.loc[:asof]
        if len(frame) < 2:
            continue
        previous, current = frame.iloc[-2], frame.iloc[-1]
        for key in keys:
            before, after = bool(previous[key]), bool(current[key])
            if before != after:
                result.append(
                    {
                        "ticker": ticker,
                        "key": key,
                        "previous_value": before,
                        "current_value": after,
                        "observation_date": _iso(frame.index[-1]),
                    }
                )
    return result


def _breadth(prepared, asof):
    above20 = []
    above50 = []
    high_low = []
    for ticker, item in prepared.items():
        if ticker in REFERENCE_TICKERS:
            continue
        frame = item.history.loc[:asof]
        if frame.empty:
            continue
        date = frame.index[-1]
        close = float(frame["Close"].iloc[-1])
        ema = _series_at(item.ema20, date)
        sma = _series_at(item.sma50, date)
        if ema is not None:
            above20.append(close > ema)
        if sma is not None:
            above50.append(close > sma)
        if len(frame) >= 20:
            window = frame["Close"].astype(float).iloc[-20:]
            high_low.append(
                1.0
                if close >= float(window.max())
                else -1.0
                if close <= float(window.min())
                else 0.0
            )
    return (
        _boolean_mean(above20),
        _boolean_mean(above50),
        None if not high_low else float(np.mean(high_low)),
    )


def _numeric_evidence(
    key,
    value,
    weight,
    met_threshold,
    near_threshold,
    window,
    *,
    direction="high",
):
    checked = _finite_or_none(value)
    if checked is None:
        return Evidence(
            key,
            None,
            float(met_threshold),
            "unavailable",
            0.0,
            weight,
            window,
            "insufficient_history",
            {"near_threshold": near_threshold},
        )
    if direction == "high":
        state = (
            "met"
            if checked >= met_threshold
            else "near"
            if checked >= near_threshold
            else "unmet"
        )
    else:
        state = (
            "met"
            if checked <= met_threshold
            else "near"
            if checked <= near_threshold
            else "unmet"
        )
    points = weight if state == "met" else weight / 2.0 if state == "near" else 0.0
    return Evidence(
        key,
        checked,
        float(met_threshold),
        state,
        points,
        weight,
        window,
        metadata={
            "near_threshold": near_threshold,
            "direction": direction,
        },
    )


def _bool_evidence(key, value, weight, window):
    if value is None:
        return Evidence(
            key,
            None,
            None,
            "unavailable",
            0.0,
            weight,
            window,
            "insufficient_history",
        )
    checked = bool(value)
    return Evidence(
        key,
        checked,
        None,
        "met" if checked else "unmet",
        weight if checked else 0.0,
        weight,
        window,
    )


def _above_average(item, asof, average):
    if item is None:
        return None
    row = item.history.loc[:asof]
    if row.empty:
        return None
    date = row.index[-1]
    mean = _series_at(getattr(item, average), date)
    return None if mean is None else float(row["Close"].iloc[-1]) > mean


def _cross(item, asof, average, *, direction):
    if item is None:
        return None
    close = item.history["Close"].loc[:asof]
    mean = getattr(item, average).loc[:asof]
    aligned = pd.concat((close, mean), axis=1).dropna()
    if len(aligned) < 2:
        return None
    previous, current = aligned.iloc[-2], aligned.iloc[-1]
    if direction == "up":
        return bool(previous.iloc[0] <= previous.iloc[1] and current.iloc[0] > current.iloc[1])
    return bool(previous.iloc[0] >= previous.iloc[1] and current.iloc[0] < current.iloc[1])


def _not_new_low(item, asof, window):
    if item is None:
        return None
    close = item.history["Close"].loc[:asof].astype(float)
    if len(close) < window + 1:
        return None
    return bool(close.iloc[-1] > close.iloc[-window - 1:-1].min())


def _downside_range_contracting(item, asof):
    if item is None:
        return None
    history = item.history.loc[:asof]
    if len(history) < 11:
        return None
    previous = history["Close"].shift(1)
    downside = pd.concat(
        (
            history["High"] - history["Low"],
            (history["Low"] - previous).abs(),
        ),
        axis=1,
    ).max(axis=1)
    return bool(downside.iloc[-5:].mean() < downside.iloc[-10:-5].mean())


def _atr_ratio(item, asof):
    if item is None:
        return None
    atr = item.atr20.loc[:asof].dropna()
    if len(atr) < 63:
        return None
    median = float(atr.iloc[-63:].median())
    return None if median == 0.0 else float(atr.iloc[-1]) / median


def _relative_return(sector, benchmark, asof, window):
    if sector is None or benchmark is None:
        return None
    sector_return = _return_at(sector, asof, window)
    benchmark_return = _return_at(
        benchmark.history["Close"],
        asof,
        window,
    )
    if sector_return is None or benchmark_return is None:
        return None
    return sector_return - benchmark_return


def _relative_slope(sector, benchmark, asof, window):
    if sector is None or benchmark is None:
        return None
    aligned = pd.concat(
        (
            sector.rename("sector"),
            benchmark.history["Close"].astype(float).rename("benchmark"),
        ),
        axis=1,
        join="inner",
    ).loc[:asof].dropna()
    if len(aligned) < window:
        return None
    ratio = aligned["sector"].iloc[-window:] / aligned["benchmark"].iloc[-window:]
    if (ratio <= 0.0).any():
        return None
    return float(np.polyfit(np.arange(window), np.log(ratio), 1)[0])


def _stock_sector_relative_return(close, sector, asof, window):
    if sector is None:
        return None
    aligned = pd.concat(
        (close.astype(float).rename("stock"), sector.rename("sector")),
        axis=1,
        join="inner",
    ).loc[:asof].dropna()
    if len(aligned) <= window:
        return None
    return float(
        aligned["stock"].iloc[-1] / aligned["stock"].iloc[-window - 1] - 1.0
        - (
            aligned["sector"].iloc[-1] / aligned["sector"].iloc[-window - 1]
            - 1.0
        )
    )


def _stock_sector_breakdown(close, sector, asof):
    if sector is None:
        return None
    aligned = pd.concat(
        (close.astype(float).rename("stock"), sector.rename("sector")),
        axis=1,
        join="inner",
    ).loc[:asof].dropna()
    if len(aligned) < 21:
        return None
    ratio = aligned["stock"] / aligned["sector"].replace(0.0, np.nan)
    average = ratio.rolling(20, min_periods=20).mean()
    if not np.isfinite(average.iloc[-2:]).all():
        return None
    return bool(
        ratio.iloc[-2] >= average.iloc[-2]
        and ratio.iloc[-1] < average.iloc[-1]
    )


def _return_at(series, asof, window):
    if series is None:
        return None
    values = series.loc[:asof].dropna().astype(float)
    if len(values) <= window:
        return None
    prior = float(values.iloc[-window - 1])
    return None if prior == 0.0 else float(values.iloc[-1] / prior - 1.0)


def _daily_return(close, asof):
    values = close.loc[:asof].dropna().astype(float)
    if len(values) < 2 or float(values.iloc[-2]) == 0.0:
        return None
    return float(values.iloc[-1] / values.iloc[-2] - 1.0)


def _tail_sum(series, asof, window):
    values = series.loc[:asof]
    if len(values) < window:
        return None
    return float(values.iloc[-window:].astype(float).sum())


def _series_at(series, asof):
    values = series.loc[:asof].dropna()
    return None if values.empty else _finite_or_none(values.iloc[-1])


def _row_at(frame, asof):
    rows = frame.loc[:asof]
    return None if rows.empty else rows.iloc[-1]


def _row_bool(row, key):
    return None if row is None or key not in row else bool(row[key])


def _common_asof(prepared, cutoff):
    dates = [
        item.history.index[-1]
        for item in prepared.values()
        if not item.history.empty
    ]
    if cutoff is not None:
        dates.append(cutoff)
    return None if not dates else min(dates) if cutoff is None else cutoff


def _aggregate_score(scores, coverage):
    if not scores:
        return {
            "score": None,
            "coverage": coverage,
            "unavailable_reason": "insufficient_coverage",
        }
    return {
        "score": round(float(np.mean(scores)), 2),
        "coverage": coverage,
        "unavailable_reason": None,
    }


def _aggregate_risk_state(constituents, state_score, raw_score):
    if state_score is None:
        return "unavailable"
    if state_score < RISK_MEMORY_ACTIVE_THRESHOLD:
        return "inactive"
    states = {
        row["downside_risk"].get("state")
        for row in constituents
    }
    if "new" in states:
        return "new"
    if "persistent" in states:
        return "persistent"
    if raw_score is not None and state_score > raw_score:
        return "fading"
    return "persistent"


def _evidence_dict(item):
    return {
        "key": item.key,
        "value": _json_scalar(item.value),
        "threshold": _json_scalar(item.threshold),
        "state": item.state,
        "points": float(item.points),
        "max_points": float(item.max_points),
        "window": item.window,
        "unavailable_reason": item.unavailable_reason,
        "metadata": {
            str(key): _json_scalar(value)
            for key, value in item.metadata.items()
        },
    }


def _boolean_mean(values):
    return None if not values else float(np.mean(values))


def _finite_or_none(value):
    if value is None or isinstance(value, (bool, np.bool_)):
        return None if value is None else bool(value)
    try:
        checked = float(value)
    except (TypeError, ValueError):
        return None
    return checked if np.isfinite(checked) else None


def _integer_or_none(value):
    checked = _finite_or_none(value)
    return None if checked is None else int(checked)


def _json_scalar(value):
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return _finite_or_none(value)
    return value


def _cutoff(value):
    if value is None:
        return None
    result = pd.Timestamp(value)
    if result.tzinfo is not None:
        result = result.tz_localize(None)
    return result.normalize()


def _horizon(value):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError("invalid_horizon")
    checked = int(value)
    if checked not in SUPPORTED_HORIZONS:
        raise ValueError("invalid_horizon")
    return checked


def _iso(value):
    return None if value is None else pd.Timestamp(value).date().isoformat()


def _empty_multiindex_frame(columns):
    return pd.DataFrame(
        columns=columns,
        index=pd.MultiIndex.from_arrays(
            ([], []),
            names=("ticker", "observation_date"),
        ),
    )
