"""Pure point-in-time feature and forward-label dataset builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Integral

import numpy as np
import pandas as pd

from factors.compute import _ema, _sma, _zigzag_pivots
from research.market_context import build_atomic_model_rows
from research.reversal import build_reversal_rows
from web.forecasts.base import SUPPORTED_HORIZONS
from web.market_groups import market_group


MARKET_ATOMIC_FEATURE_COLUMNS = (
    "pressure_close_location",
    "pressure_upper_wick_ratio",
    "pressure_signed_volume_proxy",
    "pressure_distribution_day",
    "pressure_failed_breakout",
    "qqq_trend_state",
    "qqq_close_vs_ema20_pct",
    "qqq_return_5",
    "qqq_return_20",
    "qqq_volume_ratio",
    "sector_trend_state",
    "sector_relative_strength_20",
    "stock_sector_relative_strength_20",
    "early_prior_session_selloff",
    "early_current_price_acceptance",
    "early_descending_trendline_proximity",
    "early_current_volume_support",
)
FEATURE_COLUMNS = (
    "close_vs_ema20_pct",
    "close_vs_sma50_pct",
    "close_vs_sma200_pct",
    "mom_3_1",
    "mom_6_1",
    "mom_12_1",
    "strict_vcp",
    "tight_platform",
    "pivot_distance_pct",
    "volume_ratio",
    "volume_change",
    "atr20_pct",
    "realized_vol_63",
    "prior_high_breakout",
    "trendline_breakout",
    "higher_low_confirmed",
    *MARKET_ATOMIC_FEATURE_COLUMNS,
)
RIDGE_V4_FEATURE_COLUMNS = (
    "close_vs_ema20_pct",
    "close_vs_sma50_pct",
    "close_vs_sma200_pct",
    "mom_3_1",
    "mom_6_1",
    "mom_12_1",
    "strict_vcp",
    "tight_platform",
    "pivot_distance_pct",
    "volume_ratio",
    "volume_change",
    "atr20_pct",
    "realized_vol_63",
    "prior_high_breakout",
    "trendline_breakout",
    "higher_low_confirmed",
    "pressure_close_location",
    "pressure_upper_wick_ratio",
    "pressure_signed_volume_proxy",
    "pressure_distribution_day",
    "pressure_failed_breakout",
    "qqq_trend_state",
    "sector_relative_strength_20",
    "stock_sector_relative_strength_20",
)
REQUIRED_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
INDEX_NAMES = ("ticker", "observation_date")


def build_feature_frame(histories: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Return causal model features keyed by ticker and observation date.

    Each row is computed only from that ticker's observations through the row's
    timestamp. Histories are never mutated, fetched, or joined across dates.
    """
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping of ticker to DataFrame")

    validated = {}
    for raw_ticker, source in histories.items():
        ticker = str(raw_ticker)
        history = _validated_history(ticker, source)
        validated[ticker] = history
    ticker_frames = []
    for ticker, history in validated.items():
        if history.empty:
            continue
        ticker_frames.append(_ticker_features(ticker, history))

    if not ticker_frames:
        index = pd.MultiIndex.from_arrays(
            [pd.Index([], dtype=object), pd.DatetimeIndex([])], names=INDEX_NAMES
        )
        return pd.DataFrame(columns=("close", *FEATURE_COLUMNS), index=index)

    result = pd.concat(ticker_frames, axis=0).sort_index()
    if result.index.has_duplicates:
        raise ValueError("duplicate (ticker, observation_date) keys are not allowed")
    market_rows = build_atomic_model_rows(
        validated,
        market_group("semiconductor"),
    )
    for column in MARKET_ATOMIC_FEATURE_COLUMNS:
        result[column] = market_rows[column].reindex(result.index)
    result = result.loc[:, ("close", *FEATURE_COLUMNS)].astype(float)
    result = result.where(np.isfinite(result), np.nan)
    return result


def attach_forward_targets(
    frame: pd.DataFrame, horizons: Sequence[int] = SUPPORTED_HORIZONS
) -> pd.DataFrame:
    """Attach session-aligned forward returns and their explicit end dates."""
    _validate_feature_frame(frame)
    checked_horizons = _validated_horizons(horizons)
    result = frame.copy(deep=True)

    for horizon in checked_horizons:
        target_name = target_column(horizon)
        end_name = label_end_column(horizon)
        future_close = result["close"].groupby(level="ticker", sort=False).shift(
            -horizon
        )
        denominator = result["close"].replace(0.0, np.nan)
        result[target_name] = future_close / denominator - 1.0
        observation_dates = pd.Series(
            result.index.get_level_values("observation_date"),
            index=result.index,
            dtype="datetime64[ns]",
        )
        result[end_name] = observation_dates.groupby(
            level="ticker", sort=False
        ).shift(-horizon)

    return result.sort_index()


def eligible_training_rows(
    frame: pd.DataFrame, asof, horizon: int, *, _labels_validated=False
) -> pd.DataFrame:
    """Return labels fully observable strictly before ``asof``.

    The stored label-end date is the source of truth. This makes the purge
    boundary auditable and prevents the forecast observation from training on
    a label that completes on the same session.
    """
    _validate_feature_frame(frame)
    horizon = _validated_horizons((horizon,))[0]
    target_name = target_column(horizon)
    end_name = label_end_column(horizon)
    missing = [name for name in (target_name, end_name) if name not in frame]
    if missing:
        raise ValueError(f"frame is missing forward-label columns: {missing}")
    cutoff = pd.Timestamp(asof)
    if pd.isna(cutoff):
        raise ValueError("asof must be a valid timestamp")
    if cutoff.tz is not None:
        cutoff = cutoff.tz_localize(None)
    cutoff = cutoff.normalize()
    if not _labels_validated:
        _validate_label_dates(frame, horizon)
    observation_dates = pd.Series(
        frame.index.get_level_values("observation_date"), index=frame.index
    )

    eligible = frame.loc[
        frame[target_name].notna()
        & frame[end_name].notna()
        & (observation_dates < frame[end_name])
        & (frame[end_name] < cutoff)
    ]
    return eligible.copy(deep=True).sort_index()


def target_column(horizon: int) -> str:
    return f"target_return_{int(horizon)}"


def label_end_column(horizon: int) -> str:
    return f"label_end_date_{int(horizon)}"


def _validated_history(ticker: str, source: pd.DataFrame) -> pd.DataFrame:
    if not ticker:
        raise ValueError("ticker must not be empty")
    if not isinstance(source, pd.DataFrame):
        raise TypeError(f"history for {ticker} must be a DataFrame")
    missing = [column for column in REQUIRED_PRICE_COLUMNS if column not in source]
    if missing:
        raise ValueError(f"history for {ticker} is missing columns: {missing}")

    history = source.loc[:, REQUIRED_PRICE_COLUMNS].copy(deep=True)
    try:
        history.index = pd.DatetimeIndex(history.index)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"history for {ticker} has invalid dates") from exc
    if history.index.has_duplicates:
        raise ValueError(
            f"history for {ticker} has duplicate observation dates"
        )
    if history.index.isna().any():
        raise ValueError(f"history for {ticker} has missing observation dates")
    if history.index.tz is not None:
        history.index = history.index.tz_localize(None)
    return history.sort_index().astype(float)


def _ticker_features(ticker: str, history: pd.DataFrame) -> pd.DataFrame:
    close = history["Close"]
    volume = history["Volume"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            history["High"] - history["Low"],
            (history["High"] - previous_close).abs(),
            (history["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr20 = true_range.rolling(20).mean()
    atr20.iloc[:20] = np.nan
    ema20 = _ema(close, 20)
    sma50 = _sma(close, 50)
    sma200 = _sma(close, 200)
    pivot = close.shift(1).rolling(20).max()
    volume_ratio = volume / volume.rolling(20).mean()
    features = pd.DataFrame(index=history.index)
    features["close"] = close
    features["close_vs_ema20_pct"] = _relative_percent(close, ema20)
    features["close_vs_sma50_pct"] = _relative_percent(close, sma50)
    features["close_vs_sma200_pct"] = _relative_percent(close, sma200)
    features["mom_3_1"] = close.shift(21) / close.shift(63) - 1.0
    features["mom_6_1"] = close.shift(21) / close.shift(126) - 1.0
    features["mom_12_1"] = close.shift(21) / close.shift(252) - 1.0
    features["pivot_distance_pct"] = _relative_percent(close, pivot)
    features["volume_ratio"] = volume_ratio
    features["volume_change"] = volume.pct_change(fill_method=None)
    features["atr20_pct"] = atr20 / close.replace(0.0, np.nan) * 100.0
    features["realized_vol_63"] = (
        close.pct_change(fill_method=None)
        .rolling(63, min_periods=40)
        .std(ddof=1)
        * np.sqrt(252)
    )
    strict_vcp, platform = _fast_structure_features(history)
    features["strict_vcp"] = strict_vcp
    features["tight_platform"] = platform
    reversal = pd.DataFrame(build_reversal_rows(history), index=history.index)
    for key in (
        "prior_high_breakout",
        "trendline_breakout",
        "higher_low_confirmed",
    ):
        features[key] = reversal[key].astype(float)
    for key in MARKET_ATOMIC_FEATURE_COLUMNS:
        features[key] = np.nan
    features = features.loc[:, ("close", *FEATURE_COLUMNS)].astype(float)
    features = features.where(np.isfinite(features), np.nan)
    features.index = pd.MultiIndex.from_arrays(
        [[ticker] * len(features), features.index], names=INDEX_NAMES
    )
    return features


def _fast_structure_features(history: pd.DataFrame):
    """Compute exact causal structure flags without rebuilding DataFrames.

    The public factor analyzers return rich diagnostic dictionaries.  The
    forecast dataset consumes only their two boolean decisions, so this path
    ports the same gates to bounded NumPy slices while retaining the exact
    252-session, expanding-prefix information boundary.
    """
    vcp_values = np.full(len(history), np.nan, dtype=float)
    platform_values = np.full(len(history), np.nan, dtype=float)
    values = history.loc[:, REQUIRED_PRICE_COLUMNS].to_numpy(dtype=float, copy=False)
    high = values[:, 1]
    low = values[:, 2]
    close = values[:, 3]
    positions = np.arange(len(history))
    close_series = pd.Series(close, index=history.index, dtype=float)
    finite_rows = np.isfinite(values).all(axis=1).astype(float)
    finite_lookback = (
        pd.Series(finite_rows, index=history.index)
        .rolling(252, min_periods=1)
        .min()
        .to_numpy(dtype=float)
        == 1.0
    )
    ma50 = close_series.rolling(50).mean().to_numpy(dtype=float)
    ma200 = close_series.rolling(200).mean().to_numpy(dtype=float)
    high_52w = close_series.rolling(252, min_periods=1).max().to_numpy(dtype=float)
    return_20 = (close_series / close_series.shift(20) - 1.0).to_numpy(dtype=float)
    common_gate = (
        (positions >= 59)
        & finite_lookback
        & (close > ma50)
        & (np.isnan(ma200) | (ma50 >= ma200))
        & (return_20 <= 0.12)
    )
    vcp_candidates = common_gate & (close / high_52w >= 0.75)
    for position in np.flatnonzero(vcp_candidates):
        start = max(0, position - 251)
        window_high = high[start : position + 1]
        window_low = low[start : position + 1]
        window_close = close[start : position + 1]
        vcp_values[position] = float(
            _strict_vcp_pattern(window_high, window_low, window_close)
        )
    vcp_values[(positions >= 59) & finite_lookback & ~vcp_candidates] = 0.0

    previous_close = close_series.shift(1)
    true_range = pd.concat(
        (
            pd.Series(high - low, index=history.index),
            pd.Series(np.abs(high - previous_close), index=history.index),
            pd.Series(np.abs(low - previous_close), index=history.index),
        ),
        axis=1,
    ).max(axis=1)
    atr20 = true_range.rolling(20).mean().to_numpy(dtype=float)
    fallback_atr = close * 0.02
    effective_atr = np.where((atr20 != 0.0) & np.isfinite(atr20), atr20, fallback_atr)
    window_high = close_series.rolling(20).max().to_numpy(dtype=float)
    window_low = close_series.rolling(20).min().to_numpy(dtype=float)
    range_pct = (window_high - window_low) / window_high * 100.0
    base_return = (close_series / close_series.shift(19) - 1.0).to_numpy(dtype=float)
    travel = close_series.diff().abs().rolling(19).sum().to_numpy(dtype=float)
    efficiency = np.ones(len(history), dtype=float)
    np.divide(
        np.abs(close - close_series.shift(19).to_numpy(dtype=float)),
        travel,
        out=efficiency,
        where=travel > 0.0,
    )
    platform_candidates = (
        common_gate
        & (close / high_52w >= 0.90)
        & (range_pct <= np.maximum(6.0, 4.0 * effective_atr / close * 100.0))
        & (base_return <= 0.08)
        & (efficiency <= 0.35)
    )
    platform_values[(positions >= 59) & finite_lookback] = platform_candidates[
        (positions >= 59) & finite_lookback
    ].astype(float)
    return (
        pd.Series(vcp_values, index=history.index, dtype=float),
        pd.Series(platform_values, index=history.index, dtype=float),
    )


def _strict_vcp_pattern(high, low, close):
    segment_start = max(0, len(close) - 250)
    segment_high = high[segment_start:]
    segment_low = low[segment_start:]
    segment_close = close[segment_start:]
    chosen = None
    for base_days in range(80, 19, -5):
        if base_days > len(segment_close) - 1:
            continue
        base_high = segment_high[-base_days:]
        base_low = segment_low[-base_days:]
        base_close = segment_close[-base_days:]
        highest = float(np.max(base_high))
        lowest = float(np.min(base_low))
        depth = (highest - lowest) / highest if highest else 1.0
        if depth > 0.35:
            continue
        base_return = base_close[-1] / base_close[0] - 1.0
        travel = float(np.sum(np.abs(np.diff(base_close))))
        efficiency = (
            abs(base_close[-1] - base_close[0]) / travel if travel > 0.0 else 1.0
        )
        if base_return > 0.15 and efficiency > 0.50:
            continue
        chosen = (base_high, base_low, base_close)
        break
    if chosen is None:
        return False

    base_high, base_low, base_close = chosen
    atr = _array_atr(base_high, base_low, base_close, min(20, len(base_close) - 1))
    if not atr:
        atr = base_close[-1] * 0.03
    adaptive_pct = min(max(atr / base_close[-1] * 150.0, 3.0), 10.0)
    pivots = _zigzag_pivots(base_close, pct=adaptive_pct)
    if len(pivots) < 3:
        return False
    contractions = []
    for left, right in zip(pivots, pivots[1:]):
        if left[2] == "H" and right[2] == "L":
            depth = (left[1] - right[1]) / left[1] * 100.0
            if depth > 2.0:
                contractions.append(round(float(depth), 1))
    if len(contractions) < 2:
        return False
    contractions = contractions[-4:]
    strictly_decreasing = all(
        contractions[index + 1] <= contractions[index] * 0.95
        for index in range(len(contractions) - 1)
    )
    last_first_ratio = contractions[-1] / contractions[0]
    return bool(
        strictly_decreasing
        and last_first_ratio <= 0.75
        and contractions[0] - contractions[-1] >= 3.0
    )


def _array_atr(high, low, close, periods):
    if len(close) < periods + 1:
        return None
    previous_close = np.empty_like(close, dtype=float)
    previous_close[0] = np.nan
    previous_close[1:] = close[:-1]
    true_range = np.fmax.reduce(
        (
            high - low,
            np.abs(high - previous_close),
            np.abs(low - previous_close),
        )
    )
    return float(np.mean(true_range[-periods:]))


def _relative_percent(numerator: pd.Series, denominator: pd.Series):
    return numerator / denominator.replace(0.0, np.nan) * 100.0 - 100.0


def _validate_feature_frame(frame: pd.DataFrame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    if not isinstance(frame.index, pd.MultiIndex) or tuple(frame.index.names) != INDEX_NAMES:
        raise ValueError(
            "frame index must be a MultiIndex named ticker and observation_date"
        )
    if frame.index.has_duplicates:
        raise ValueError("duplicate (ticker, observation_date) keys are not allowed")
    observation_dates = frame.index.get_level_values("observation_date")
    if not pd.api.types.is_datetime64_any_dtype(observation_dates.dtype):
        raise ValueError("observation_date keys must be datetime values")
    if observation_dates.isna().any():
        raise ValueError("observation_date keys must not be missing")
    if "close" not in frame:
        raise ValueError("frame must include close")


def _validated_horizons(horizons: Sequence[int]):
    result = tuple(horizons)
    if not result:
        raise ValueError("at least one horizon is required")
    if any(isinstance(value, bool) or not isinstance(value, Integral) for value in result):
        raise ValueError("forecast horizons must be integers")
    result = tuple(int(value) for value in result)
    if len(set(result)) != len(result):
        raise ValueError("duplicate horizons are not allowed")
    invalid = [value for value in result if value not in SUPPORTED_HORIZONS]
    if invalid:
        raise ValueError(f"unsupported forecast horizons: {invalid}")
    return result


def _validate_label_dates(frame: pd.DataFrame, horizon: int):
    end_name = label_end_column(horizon)
    if not pd.api.types.is_datetime64_any_dtype(frame[end_name].dtype):
        raise ValueError(f"{end_name} must contain datetime values")

    for _, group in frame.groupby(level="ticker", sort=False):
        ordered = group.sort_index(level="observation_date")
        observation_dates = pd.Series(
            ordered.index.get_level_values("observation_date"), index=ordered.index
        )
        actual = ordered[end_name]
        comparable = actual.notna()
        if (actual.loc[comparable] <= observation_dates.loc[comparable]).any():
            raise ValueError(f"{end_name} must be after observation_date")
        expected = observation_dates.shift(-horizon)
        aligned = actual.eq(expected) | (actual.isna() & expected.isna())
        if not aligned.all():
            raise ValueError(
                f"{end_name} must match ticker-local {horizon}-session alignment"
            )
