from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3

import pandas as pd

from research import DETECTOR_VERSION, FEATURE_SPEC_VERSION
from research.events import VCPEvent, scan_ticker_events
from research.market_data import load_price_panel
from research.momentum import add_cross_sectional_ranks, momentum_features
from research.outcomes import barrier_outcome, forward_outcomes
from research.vcp import detect_vcp


def _observation_date(event: VCPEvent, stage: str) -> pd.Timestamp | None:
    if stage == "forming":
        return event.first_seen
    if stage == "near_pivot":
        return event.near_pivot_date
    if stage == "breakout":
        return event.breakout_date
    if stage == "invalidated":
        return event.invalidated_date
    return None


def _pattern_columns(pattern) -> dict:
    metrics = pattern.metrics
    return {
        "base_start": pattern.base_start,
        "base_end": pattern.base_end,
        "pivot": pattern.pivot,
        "pivot_date": pattern.pivot_date,
        "distance_to_pivot_pct": pattern.distance_to_pivot_pct,
        "n_legs": len(pattern.legs),
        "leg_depths": ";".join(f"{leg.depth_pct:.4f}" for leg in pattern.legs),
        "base_depth_pct": metrics.get("base_depth_pct"),
        "last_first_ratio": metrics.get("last_first_ratio"),
        "contraction_slope": metrics.get("contraction_slope"),
        "terminal_range_pct": metrics.get("terminal_range_pct"),
        "volume_dryup_ratio": metrics.get("volume_dryup_ratio"),
        "adaptive_pct": metrics.get("adaptive_pct"),
    }


def build_event_table(
    panel: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    tickers,
    stages=("near_pivot",),
    min_history: int = 252,
) -> pd.DataFrame:
    """Build one immutable research row for each event/stage observation."""
    rows = []
    for ticker in tickers:
        history = panel.get(ticker)
        if history is None or history.empty:
            continue
        for event in scan_ticker_events(ticker, history, min_history=min_history):
            for stage in stages:
                date = _observation_date(event, stage)
                if date is None:
                    continue
                known = history.loc[history.index <= date]
                pattern = detect_vcp(known)
                if not pattern.accepted:
                    pattern = event.initial_pattern
                row = {
                    "event_id": event.event_id if len(stages) == 1 else f"{event.event_id}:{stage}",
                    "ticker": ticker,
                    "observation_stage": stage,
                    "observation_date": pd.Timestamp(date),
                    "detector_version": DETECTOR_VERSION,
                    "feature_spec_version": FEATURE_SPEC_VERSION,
                    "breakout_volume_ratio": event.breakout_volume_ratio,
                    "volume_confirmed": event.volume_confirmed,
                }
                row.update(_pattern_columns(pattern))
                row.update(momentum_features(history, benchmark, date))
                row.update(forward_outcomes(history, benchmark, date))
                row.update(barrier_outcome(history, date))
                rows.append(row)
    if not rows:
        return pd.DataFrame()
    table = pd.DataFrame(rows)
    table = add_cross_sectional_ranks(table)
    return table.sort_values(["observation_date", "ticker"]).reset_index(drop=True)


def _tickers_in_db(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT ticker FROM prices WHERE ticker <> 'SPY' ORDER BY ticker"
            )
        ]


def _write_versioned(table: pd.DataFrame, output: str, force: bool) -> None:
    path = Path(output)
    if path.exists() and not force:
        raise FileExistsError(f"output already exists: {path}; pass --force to replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    table.to_csv(temporary, index=False)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/prices.db")
    parser.add_argument("--output", default="output/research/vcp_events_v1.csv")
    parser.add_argument("--stages", nargs="+", default=["near_pivot"])
    parser.add_argument("--min-history", type=int, default=252)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    tickers = _tickers_in_db(args.db)
    panel = load_price_panel(args.db, tickers + ["SPY"])
    benchmark = panel.pop("SPY")
    table = build_event_table(
        panel, benchmark, tickers, stages=tuple(args.stages), min_history=args.min_history
    )
    _write_versioned(table, args.output, args.force)
    if table.empty:
        print(f"tickers={len(tickers)} events=0 output={args.output}")
        return
    print(
        f"tickers={len(tickers)} events={len(table)} "
        f"stages={table.observation_stage.value_counts().to_dict()} "
        f"dates={table.observation_date.min().date()}..{table.observation_date.max().date()} "
        f"missing_mom12={int(table.mom_12_1.isna().sum())} output={args.output}"
    )


if __name__ == "__main__":
    main()
