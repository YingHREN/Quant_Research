"""Matured market-score outcomes and empirical calibration."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral

import numpy as np
import pandas as pd


SUPPORTED_HORIZONS = (5, 20, 60)
POSITIVE_BANDS = {5: 0.01, 20: 0.02, 60: 0.04}
OUTCOMES = ("opportunity", "downside_risk")


@dataclass(frozen=True)
class ScoreCalibration:
    probability: float | None
    reason: str | None
    sample_count: int
    training_cutoff: str | None

    def to_dict(self):
        return {
            "probability": self.probability,
            "reason": self.reason,
            "sample_count": self.sample_count,
            "training_cutoff": self.training_cutoff,
        }


def attach_market_outcomes(
    score_frame,
    histories,
    horizons=SUPPORTED_HORIZONS,
):
    """Attach terminal opportunity and path-dependent downside outcomes."""
    _validate_score_frame(score_frame)
    checked_horizons = tuple(_horizon(value) for value in horizons)
    if len(set(checked_horizons)) != len(checked_horizons):
        raise ValueError("horizons must be unique")
    result = score_frame.copy(deep=True)
    if result.empty:
        for horizon in checked_horizons:
            result[f"opportunity_outcome_{horizon}"] = pd.Series(
                index=result.index,
                dtype=float,
            )
            result[f"downside_risk_outcome_{horizon}"] = pd.Series(
                index=result.index,
                dtype=float,
            )
            result[f"opportunity_label_end_date_{horizon}"] = pd.Series(
                index=result.index,
                dtype="datetime64[ns]",
            )
            result[f"downside_risk_label_end_date_{horizon}"] = pd.Series(
                index=result.index,
                dtype="datetime64[ns]",
            )
        return result
    tickers = set(score_frame.index.get_level_values("ticker"))
    close_frames = {}
    for raw_ticker, history in dict(histories).items():
        ticker = str(raw_ticker)
        if ticker not in tickers:
            continue
        if not isinstance(history, pd.DataFrame) or "Close" not in history:
            raise ValueError(f"history for {ticker} requires Close")
        series = history["Close"].astype(float).copy()
        series.index = pd.DatetimeIndex(series.index).tz_localize(None)
        close_frames[ticker] = series.sort_index()

    if close_frames:
        close = pd.concat(
            close_frames,
            names=("ticker", "observation_date"),
        ).reindex(score_frame.index)
    else:
        close = pd.Series(np.nan, index=score_frame.index, dtype=float)
    observation_dates = pd.Series(
        score_frame.index.get_level_values("observation_date"),
        index=score_frame.index,
        dtype="datetime64[ns]",
    )

    for horizon in checked_horizons:
        terminal = close.groupby(
            level="ticker",
            sort=False,
        ).shift(-horizon)
        label_end = observation_dates.groupby(
            level="ticker",
            sort=False,
        ).shift(-horizon)
        denominator = close.replace(0.0, np.nan)
        forward_return = terminal / denominator - 1.0
        forward_min = close.groupby(
            level="ticker",
            sort=False,
        ).transform(
            lambda series: (
                series.shift(-1)[::-1]
                .rolling(horizon, min_periods=horizon)
                .min()[::-1]
            )
        )
        forward_drawdown = forward_min / denominator - 1.0
        risk_barrier = pd.Series(
            np.maximum(
                POSITIVE_BANDS[horizon],
                result["atr20_pct"].to_numpy(dtype=float) / 100.0,
            ),
            index=result.index,
            dtype=float,
        )
        opportunity_complete = terminal.notna() & denominator.notna()
        risk_complete = (
            opportunity_complete
            & forward_min.notna()
            & np.isfinite(risk_barrier)
        )
        result[f"opportunity_outcome_{horizon}"] = (
            (forward_return > POSITIVE_BANDS[horizon])
            .astype(float)
            .where(opportunity_complete)
        )
        result[f"downside_risk_outcome_{horizon}"] = (
            (forward_drawdown < -risk_barrier)
            .astype(float)
            .where(risk_complete)
        )
        result[f"opportunity_label_end_date_{horizon}"] = label_end.where(
            opportunity_complete
        )
        result[f"downside_risk_label_end_date_{horizon}"] = label_end.where(
            risk_complete
        )
    return result.sort_index()


def eligible_outcome_rows(frame, asof, horizon, outcome):
    checked_horizon = _horizon(horizon)
    checked_outcome = _outcome(outcome)
    cutoff = pd.Timestamp(asof)
    if pd.isna(cutoff):
        raise ValueError("asof must be a valid timestamp")
    if cutoff.tz is not None:
        cutoff = cutoff.tz_localize(None)
    cutoff = cutoff.normalize()
    end = f"{checked_outcome}_label_end_date_{checked_horizon}"
    target = f"{checked_outcome}_outcome_{checked_horizon}"
    missing = [column for column in (end, target) if column not in frame]
    if missing:
        raise ValueError(f"frame is missing outcome columns: {missing}")
    rows = frame.loc[
        frame[target].notna()
        & frame[end].notna()
        & (frame[end] < cutoff)
    ]
    return rows.sort_index().copy(deep=True)


def calibrate_score_probability(
    frame,
    current_score,
    asof,
    horizon,
    outcome,
    minimum_samples=100,
):
    checked_horizon = _horizon(horizon)
    checked_outcome = _outcome(outcome)
    if isinstance(minimum_samples, bool) or not isinstance(
        minimum_samples,
        Integral,
    ):
        raise TypeError("minimum_samples must be an integer")
    minimum = max(100, int(minimum_samples))
    if current_score is None:
        return ScoreCalibration(None, "score_unavailable", 0, None)
    query = float(current_score)
    if not np.isfinite(query):
        return ScoreCalibration(None, "score_unavailable", 0, None)
    rows = eligible_outcome_rows(
        frame,
        asof,
        checked_horizon,
        checked_outcome,
    )
    score_column = (
        "reversal_opportunity_score"
        if checked_outcome == "opportunity"
        else "downside_risk_score"
    )
    target_column = (
        f"{checked_outcome}_outcome_{checked_horizon}"
    )
    missing = [
        column
        for column in (score_column, target_column)
        if column not in rows
    ]
    if missing:
        raise ValueError(f"frame is missing calibration columns: {missing}")
    pairs = rows.loc[:, (score_column, target_column)].dropna()
    finite = np.isfinite(pairs.to_numpy(dtype=float)).all(axis=1)
    pairs = pairs.loc[finite]
    if len(pairs) < minimum:
        return ScoreCalibration(
            None,
            "insufficient_calibration_samples",
            len(pairs),
            None,
        )
    classes = set(pairs[target_column].astype(int))
    if classes != {0, 1}:
        return ScoreCalibration(
            None,
            "calibration_requires_both_classes",
            len(pairs),
            None,
        )
    probability = _isotonic_probability(
        pairs[score_column].to_numpy(dtype=float),
        pairs[target_column].to_numpy(dtype=float),
        query,
    )
    cutoff = pairs.index.get_level_values("observation_date").max()
    return ScoreCalibration(
        probability,
        None,
        len(pairs),
        pd.Timestamp(cutoff).date().isoformat(),
    )


def _isotonic_probability(scores, outcomes, query):
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    sorted_outcomes = outcomes[order]
    unique_scores, first = np.unique(sorted_scores, return_index=True)
    counts = np.diff(np.append(first, len(sorted_scores))).astype(float)
    sums = np.add.reduceat(sorted_outcomes, first)
    blocks = []
    for score, total, count in zip(unique_scores, sums, counts):
        blocks.append([float(score), float(total), float(count)])
        while (
            len(blocks) >= 2
            and blocks[-2][1] / blocks[-2][2]
            > blocks[-1][1] / blocks[-1][2]
        ):
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                [
                    right[0],
                    left[1] + right[1],
                    left[2] + right[2],
                ]
            )
    bounds = np.asarray([block[0] for block in blocks], dtype=float)
    levels = np.asarray(
        [block[1] / block[2] for block in blocks],
        dtype=float,
    )
    position = int(np.searchsorted(bounds, query, side="left"))
    position = min(position, len(levels) - 1)
    return float(np.clip(levels[position], 0.0, 1.0))


def _validate_score_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("score_frame must be a DataFrame")
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != [
        "ticker",
        "observation_date",
    ]:
        raise ValueError(
            "score_frame requires ticker and observation_date index levels"
        )
    if frame.index.has_duplicates:
        raise ValueError("score_frame index must be unique")
    required = (
        "reversal_opportunity_score",
        "downside_risk_score",
        "atr20_pct",
    )
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"score_frame is missing columns: {missing}")


def _horizon(value):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("invalid_horizon")
    checked = int(value)
    if checked not in SUPPORTED_HORIZONS:
        raise ValueError("invalid_horizon")
    return checked


def _outcome(value):
    if value not in OUTCOMES:
        raise ValueError("invalid_outcome")
    return str(value)
