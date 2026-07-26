"""Reproducible point-in-time evaluation for unified downside-risk sources."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from research.expanded_market_data import ExpandedMarketDataRepository
from web.forecasts.decision import (
    SOURCE_HIGH_THRESHOLDS,
    build_forecast_risk_context,
)
from web.services.market_data import MarketDataRepository
from web.market_groups import (
    REFERENCE_TICKERS,
    market_group_for_ticker,
    modeled_market_groups,
)


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
        entry_open = aligned["Open"].astype(float).shift(-1).replace(
            0.0,
            np.nan,
        )
        frame["future_return"] = (
            aligned["Close"].astype(float).shift(-horizon)
            / entry_open
            - 1.0
        )
        future_lows = pd.concat(
            [
                aligned["Low"].astype(float).shift(-offset)
                / entry_open
                - 1.0
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
    predicted = pd.Series(signal, dtype="boolean")
    actual = pd.Series(outcome, dtype="boolean")
    valid = predicted.notna() & actual.notna()
    predicted = predicted.loc[valid].astype(bool)
    actual = actual.loc[valid].astype(bool)
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
            labeled["individual_risk_score"],
            SOURCE_HIGH_THRESHOLDS["individual"],
        ),
        (
            "group",
            labeled["group_risk_score"],
            SOURCE_HIGH_THRESHOLDS["group"],
        ),
        (
            "slow_decline",
            labeled["slow_decline_risk_score"],
            SOURCE_HIGH_THRESHOLDS["slow_decline"],
        ),
        (
            "unified_policy_high",
            labeled["policy_high"],
            None,
        ),
    )
    outcome = labeled["future_mae"] <= adverse_threshold
    rows = []
    source_available = labeled[
        [
            "individual_risk_score",
            "group_risk_score",
            "slow_decline_risk_score",
        ]
    ].notna().any(axis=1)
    for key, values, threshold in definitions:
        available = (
            source_available if threshold is None else values.notna()
        )
        signal = (
            values.astype("boolean")
            if threshold is None
            else values >= threshold
        )
        metrics = binary_metrics(signal.loc[available], outcome.loc[available])
        selected = labeled.loc[available & signal.fillna(False)]
        metrics.update(
            {
                "source": key,
                "coverage": _ratio(int(available.sum()), len(labeled)),
                "mean_future_return": _finite_mean(
                    selected["future_return"]
                ),
                "mean_future_mae": _finite_mean(selected["future_mae"]),
            }
        )
        rows.append(metrics)
    return pd.DataFrame(rows).set_index("source")


def evaluation_rows_by_scope(frame, adverse_threshold=-0.05):
    """Report fixed risk rules separately for the two modeled groups."""
    scopes = {"all": frame}
    tickers = (
        frame["ticker"]
        if "ticker" in frame
        else pd.Series(
            frame.index.get_level_values("ticker"),
            index=frame.index,
        )
    )
    group_map = {}
    for ticker in pd.unique(tickers):
        group = market_group_for_ticker(ticker)
        group_map[ticker] = None if group is None else group.key
    mapped = pd.Series(tickers, index=frame.index).map(group_map.get)
    for scope in ("semiconductor", "software"):
        selected = frame.loc[mapped == scope]
        if not selected.empty:
            scopes[scope] = selected
    rows = []
    for scope, selected in scopes.items():
        metrics = evaluation_rows(
            selected,
            adverse_threshold=adverse_threshold,
        ).reset_index()
        metrics.insert(0, "scope", scope)
        rows.append(metrics)
    return pd.concat(rows, ignore_index=True)


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
    parser.add_argument("--adverse-threshold", type=float, default=-0.05)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--modeled-only", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--by-group", action="store_true")
    args = parser.parse_args(argv)
    repository = (
        ExpandedMarketDataRepository(args.database)
        if args.expanded
        else MarketDataRepository(args.database)
    )
    tickers = None
    if args.modeled_only:
        tickers = tuple(
            sorted(
                set(REFERENCE_TICKERS).union(
                    *(
                        set(group.constituent_tickers).union(
                            group.related_tickers
                        )
                        for group in modeled_market_groups()
                    )
                )
            )
        )
    if args.expanded:
        histories = repository.load_universe_histories(tickers=tickers)
    else:
        histories = repository.load_universe_histories()
        if tickers is not None:
            histories = {
                ticker: history
                for ticker, history in histories.items()
                if ticker in set(tickers)
            }
    frame = build_evaluation_frame(histories, horizon=args.horizon)
    if args.start:
        dates = frame.index.get_level_values("observation_date")
        frame = frame.loc[dates >= pd.Timestamp(args.start)]
    metrics = (
        evaluation_rows_by_scope(
            frame,
            adverse_threshold=args.adverse_threshold,
        )
        if args.by_group
        else evaluation_rows(
            frame,
            adverse_threshold=args.adverse_threshold,
        )
    )
    output = metrics.to_csv(
        index=not args.by_group,
        float_format="%.6f",
    )
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
