"""Reproducible point-in-time evaluation for unified downside-risk sources."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from web.forecasts.decision import (
    SOURCE_HIGH_THRESHOLDS,
    build_forecast_risk_context,
)
from web.services.market_data import MarketDataRepository


def build_evaluation_frame(histories, context=None, horizon=5):
    """Attach future outcomes after causal scores have already been built."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    risk = (
        build_forecast_risk_context(histories)
        if context is None
        else context.copy(deep=True)
    )
    rows = []
    for ticker in sorted(set(risk.index.get_level_values("ticker"))):
        history = histories.get(ticker)
        if history is None or history.empty:
            continue
        frame = risk.loc[ticker].copy(deep=True)
        aligned = history.reindex(frame.index)
        close = aligned["Close"].astype(float)
        frame["future_return"] = close.shift(-horizon) / close - 1.0
        future_lows = pd.concat(
            [
                aligned["Low"].astype(float).shift(-offset) / close - 1.0
                for offset in range(1, horizon + 1)
            ],
            axis=1,
        )
        frame["future_mae"] = future_lows.min(axis=1, skipna=False)
        frame["ticker"] = ticker
        rows.append(frame)
    if not rows:
        return pd.DataFrame()
    result = pd.concat(rows).sort_index()
    result["policy_high"] = (
        (
            result["individual_risk_score"]
            >= SOURCE_HIGH_THRESHOLDS["individual"]
        )
        | (
            result["group_risk_score"]
            >= SOURCE_HIGH_THRESHOLDS["group"]
        )
        | (
            result["slow_decline_risk_score"]
            >= SOURCE_HIGH_THRESHOLDS["slow_decline"]
        )
    )
    return result


def binary_metrics(signal, outcome):
    """Return transparent classification metrics without external estimators."""
    predicted = pd.Series(signal).astype(bool)
    actual = pd.Series(outcome).astype(bool)
    valid = predicted.notna() & actual.notna()
    predicted = predicted.loc[valid]
    actual = actual.loc[valid]
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    tn = int((~predicted & ~actual).sum())
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    return {
        "sample_count": int(len(predicted)),
        "signal_rate": _ratio(int(predicted.sum()), len(predicted)),
        "precision": _ratio(tp, tp + fp),
        "recall": recall,
        "specificity": specificity,
        "balanced_accuracy": (
            None
            if recall is None or specificity is None
            else (recall + specificity) / 2.0
        ),
    }


def evaluation_rows(frame, adverse_threshold=-0.05):
    """Summarize each risk source at its versioned high threshold."""
    labeled = frame.dropna(subset=["future_return", "future_mae"])
    definitions = (
        (
            "individual",
            labeled["individual_risk_score"]
            >= SOURCE_HIGH_THRESHOLDS["individual"],
        ),
        (
            "group",
            labeled["group_risk_score"]
            >= SOURCE_HIGH_THRESHOLDS["group"],
        ),
        (
            "slow_decline",
            labeled["slow_decline_risk_score"]
            >= SOURCE_HIGH_THRESHOLDS["slow_decline"],
        ),
        ("unified_policy_high", labeled["policy_high"]),
    )
    outcome = labeled["future_mae"] <= adverse_threshold
    rows = []
    for key, signal in definitions:
        metrics = binary_metrics(signal, outcome)
        selected = labeled.loc[signal.fillna(False)]
        metrics.update(
            {
                "source": key,
                "mean_future_return": _finite_mean(
                    selected["future_return"]
                ),
                "mean_future_mae": _finite_mean(selected["future_mae"]),
            }
        )
        rows.append(metrics)
    return pd.DataFrame(rows).set_index("source")


def _finite_mean(values):
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    return None if finite.empty else float(finite.mean())


def _ratio(numerator, denominator):
    return None if denominator == 0 else float(numerator) / float(denominator)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--start")
    parser.add_argument("--horizon", type=int, default=5)
    args = parser.parse_args(argv)
    histories = MarketDataRepository(args.database).load_universe_histories()
    frame = build_evaluation_frame(histories, horizon=args.horizon)
    if args.start:
        frame = frame.loc[frame.index >= pd.Timestamp(args.start)]
    print(evaluation_rows(frame).to_csv(float_format="%.6f"))


if __name__ == "__main__":
    main()
