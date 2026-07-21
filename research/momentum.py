from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


MOMENTUM_WINDOWS = {"mom_3_1": 63, "mom_6_1": 126, "mom_12_1": 252}


def _skipped_return(close: pd.Series, formation_days: int, skip_days: int = 21):
    required = formation_days + 1
    if len(close) < required:
        return None
    end = float(close.iloc[-(skip_days + 1)])
    start = float(close.iloc[-(formation_days + 1)])
    return end / start - 1 if start else None


def momentum_features(
    history: pd.DataFrame,
    benchmark: pd.DataFrame,
    asof: pd.Timestamp,
) -> dict[str, float | bool | None]:
    """Compute point-in-time momentum while keeping the latest month separate."""
    timestamp = pd.Timestamp(asof)
    stock = history.loc[history.index <= timestamp, "Close"].dropna().astype(float)
    bench = benchmark.loc[benchmark.index <= timestamp, "Close"].dropna().astype(float)
    result: dict[str, float | bool | None] = {}

    for name, formation_days in MOMENTUM_WINDOWS.items():
        value = _skipped_return(stock, formation_days)
        bench_value = _skipped_return(bench, formation_days)
        result[name] = value
        result[f"{name}_missing"] = value is None
        result[f"excess_{name}"] = (
            value - bench_value if value is not None and bench_value is not None else None
        )

    result["ret_1m"] = (
        float(stock.iloc[-1] / stock.iloc[-22] - 1) if len(stock) >= 22 else None
    )
    daily = stock.pct_change().dropna().iloc[-63:]
    realized_vol = float(daily.std(ddof=1) * np.sqrt(252)) if len(daily) >= 40 else None
    result["realized_vol_63"] = realized_vol
    for name in MOMENTUM_WINDOWS:
        value = result[name]
        result[f"vol_adjusted_{name}"] = (
            float(value) / realized_vol
            if value is not None and realized_vol is not None and realized_vol > 0
            else None
        )
    return result


def add_cross_sectional_ranks(
    rows: pd.DataFrame,
    date_col: str = "observation_date",
    features: Sequence[str] = tuple(MOMENTUM_WINDOWS),
) -> pd.DataFrame:
    """Add percentile ranks using only peers observed on the same date."""
    ranked = rows.copy()
    for feature in features:
        ranked[f"{feature}_rank"] = ranked.groupby(date_col)[feature].rank(
            pct=True, method="average"
        )
    return ranked
