from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge


VCP_FEATURES = (
    "n_legs",
    "last_first_ratio",
    "contraction_slope",
    "terminal_range_pct",
    "volume_dryup_ratio",
    "distance_to_pivot_pct",
    "base_depth_pct",
)
MOMENTUM_FEATURES = (
    "mom_3_1_rank",
    "mom_6_1_rank",
    "mom_12_1_rank",
    "ret_1m",
    "excess_mom_6_1",
    "vol_adjusted_mom_6_1",
)
INTERACTIONS = (
    ("last_first_ratio", "mom_6_1_rank"),
    ("volume_dryup_ratio", "mom_6_1_rank"),
    ("terminal_range_pct", "mom_12_1_rank"),
)
SPECIFICATIONS = ("vcp_only", "momentum_only", "vcp_momentum")


def chronological_folds(
    frame: pd.DataFrame,
    horizon: int,
    n_folds: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding chronological folds with a business-day outcome embargo."""
    dates = pd.to_datetime(frame["observation_date"])
    unique_dates = np.array(sorted(dates.dropna().unique()))
    edges = np.linspace(0, len(unique_dates), n_folds + 1, dtype=int)
    folds = []
    for fold in range(1, n_folds):
        test_dates = unique_dates[edges[fold]:edges[fold + 1]]
        if len(test_dates) == 0:
            continue
        test_start = pd.Timestamp(test_dates[0])
        cutoff = test_start - pd.offsets.BDay(horizon)
        train_index = np.flatnonzero((dates < cutoff).to_numpy())
        test_index = np.flatnonzero(dates.isin(test_dates).to_numpy())
        if len(train_index) and len(test_index):
            folds.append((train_index, test_index))
    return folds


def _raw_design(frame: pd.DataFrame, specification: str) -> pd.DataFrame:
    if specification not in SPECIFICATIONS:
        raise ValueError(f"unknown specification: {specification}")
    if specification == "vcp_only":
        columns = list(VCP_FEATURES)
    elif specification == "momentum_only":
        columns = list(MOMENTUM_FEATURES)
    else:
        columns = list(VCP_FEATURES + MOMENTUM_FEATURES)
    design = frame.reindex(columns=columns).astype(float).copy()
    if specification == "vcp_momentum":
        for left, right in INTERACTIONS:
            design[f"{left}__x__{right}"] = frame[left].astype(float) * frame[right].astype(float)
    return design


def design_matrix(frame: pd.DataFrame, specification: str, train_stats=None):
    """Impute and standardize using training-fold statistics only."""
    raw = _raw_design(frame, specification)
    if train_stats is None:
        medians = raw.median().fillna(0.0)
        imputed = raw.fillna(medians)
        means = imputed.mean()
        scales = imputed.std(ddof=0).replace(0, 1.0).fillna(1.0)
        train_stats = {"medians": medians, "means": means, "scales": scales}
    imputed = raw.fillna(train_stats["medians"])
    matrix = (imputed - train_stats["means"]) / train_stats["scales"]
    return matrix.to_numpy(dtype=float), train_stats


def walkforward_predictions(
    frame: pd.DataFrame,
    target: str = "rel_ret_40",
    horizon: int = 40,
    n_folds: int = 5,
) -> pd.DataFrame:
    common = frame.dropna(subset=[target]).copy().reset_index(drop=True)
    common["observation_date"] = pd.to_datetime(common["observation_date"])
    output = []
    for fold_number, (train_index, test_index) in enumerate(
        chronological_folds(common, horizon=horizon, n_folds=n_folds), start=1
    ):
        train = common.iloc[train_index]
        test = common.iloc[test_index]
        y_train = train[target].to_numpy(dtype=float)
        for specification in SPECIFICATIONS:
            x_train, stats = design_matrix(train, specification)
            x_test, _ = design_matrix(test, specification, train_stats=stats)
            model = Ridge(alpha=1.0, solver="lsqr")
            model.fit(x_train, y_train)
            predicted = model.predict(x_test)
            rows = test[["observation_date"]].copy()
            if "event_id" in test:
                rows["event_id"] = test["event_id"].values
            else:
                rows["event_id"] = test.index.astype(str)
            rows["actual"] = test[target].to_numpy(dtype=float)
            rows["prediction"] = predicted
            rows["train_mean"] = float(y_train.mean())
            rows["specification"] = specification
            rows["fold"] = fold_number
            output.append(rows)
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame()


def evaluate_specifications(
    frame: pd.DataFrame,
    target: str = "rel_ret_40",
    horizon: int = 40,
    n_folds: int = 5,
) -> pd.DataFrame:
    predictions = walkforward_predictions(frame, target, horizon, n_folds)
    rows = []
    if predictions.empty:
        return pd.DataFrame()
    for (specification, fold), group in predictions.groupby(["specification", "fold"]):
        actual = group["actual"].to_numpy(dtype=float)
        predicted = group["prediction"].to_numpy(dtype=float)
        baseline = group["train_mean"].to_numpy(dtype=float)
        correlation = (
            float(np.corrcoef(actual, predicted)[0, 1])
            if len(actual) > 1 and np.std(actual) > 0 and np.std(predicted) > 0
            else np.nan
        )
        denominator = float(np.square(actual - baseline).sum())
        oos_r2 = 1 - float(np.square(actual - predicted).sum()) / denominator if denominator else np.nan
        rows.append(
            {
                "specification": specification,
                "fold": int(fold),
                "n_obs": len(group),
                "correlation": correlation,
                "mae": float(np.abs(actual - predicted).mean()),
                "oos_r2": oos_r2,
            }
        )
    return pd.DataFrame(rows).sort_values(["specification", "fold"]).reset_index(drop=True)
