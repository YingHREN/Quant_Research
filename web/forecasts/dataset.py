"""Pure point-in-time feature and forward-label dataset builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Integral

import numpy as np
import pandas as pd

from factors.compute import _ema, _sma, tight_platform, vcp_analysis
from web.forecasts.base import SUPPORTED_HORIZONS


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

    ticker_frames = []
    for raw_ticker, source in histories.items():
        ticker = str(raw_ticker)
        history = _validated_history(ticker, source)
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
        result[target_name] = np.nan
        result[end_name] = pd.NaT
        for _, group in result.groupby(level="ticker", sort=False):
            ordered = group.sort_index(level="observation_date")
            future_close = ordered["close"].shift(-horizon)
            denominator = ordered["close"].replace(0.0, np.nan)
            target = future_close / denominator - 1.0
            dates = pd.Series(
                ordered.index.get_level_values("observation_date"),
                index=ordered.index,
                dtype="datetime64[ns]",
            ).shift(-horizon)
            result.loc[ordered.index, target_name] = target
            result.loc[ordered.index, end_name] = dates

    return result.sort_index()


def eligible_training_rows(
    frame: pd.DataFrame, asof, horizon: int
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
    strict_vcp, platform = _structure_features(history)
    features["strict_vcp"] = strict_vcp
    features["tight_platform"] = platform
    features = features.loc[:, ("close", *FEATURE_COLUMNS)].astype(float)
    features = features.where(np.isfinite(features), np.nan)
    features.index = pd.MultiIndex.from_arrays(
        [[ticker] * len(features), features.index], names=INDEX_NAMES
    )
    return features


def _structure_features(history: pd.DataFrame):
    vcp_values = pd.Series(np.nan, index=history.index, dtype=float)
    platform_values = pd.Series(np.nan, index=history.index, dtype=float)
    for position in range(59, len(history)):
        prefix = history.iloc[: position + 1]
        required_lookback = prefix.iloc[-252:]
        if not np.isfinite(required_lookback.to_numpy(copy=False)).all():
            continue
        vcp = vcp_analysis(required_lookback)
        platform = tight_platform(required_lookback)
        vcp_values.iloc[position] = float(vcp.get("reject_reason") is None)
        platform_values.iloc[position] = float(bool(platform.get("is_platform")))
    return vcp_values, platform_values


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
