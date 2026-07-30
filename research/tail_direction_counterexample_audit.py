"""Point-in-time matched audit for asymmetric tail-direction errors."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping

import numpy as np
import pandas as pd

from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS


HIGH_DOWN_SCORE = 0.40
TERMINAL_DOWN_THRESHOLD = -0.05
EXTREME_UP_THRESHOLD = 0.10
PATH_STRESS_THRESHOLD = -0.07
DERIVED_NUMERIC_FEATURES = (
    "opening_gap",
    "log_dollar_volume_20",
    "dollar_volume_ratio_20",
    "realized_volatility_change_20",
)
BOOLEAN_FEATURES = frozenset(
    {
        "strict_vcp",
        "tight_platform",
        "prior_high_breakout",
        "trendline_breakout",
        "higher_low_confirmed",
        "pressure_distribution_day",
        "pressure_failed_breakout",
    }
)
AUDIT_FEATURE_TYPES = OrderedDict(
    (
        feature,
        "boolean" if feature in BOOLEAN_FEATURES else "numeric",
    )
    for feature in (*RIDGE_V4_FEATURE_COLUMNS, *DERIVED_NUMERIC_FEATURES)
)


def build_audit_population(
    predictions: pd.DataFrame,
    feature_frame: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Attach causal observation features and mutually exclusive outcomes."""
    checked = _validated_predictions(predictions)
    features = _validated_feature_frame(feature_frame)
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    checked = checked.loc[
        checked["calibrated_down_probability"] >= HIGH_DOWN_SCORE
    ].copy()
    if checked.empty:
        return _empty_population(checked)

    keys = pd.MultiIndex.from_arrays(
        (
            checked["ticker"],
            checked["observation_date"],
        ),
        names=("ticker", "observation_date"),
    )
    selected_features = features.reindex(keys)
    for feature in RIDGE_V4_FEATURE_COLUMNS:
        if feature not in selected_features:
            selected_features[feature] = np.nan
        checked[feature] = pd.to_numeric(
            selected_features[feature],
            errors="coerce",
        ).to_numpy()

    for feature in DERIVED_NUMERIC_FEATURES:
        checked[feature] = np.nan
    for ticker, positions in checked.groupby(
        "ticker",
        sort=False,
    ).groups.items():
        history = histories.get(ticker)
        if history is None or history.empty:
            continue
        context = _history_context(history)
        dates = pd.DatetimeIndex(
            checked.loc[positions, "observation_date"]
        )
        for feature in (
            "opening_gap",
            "log_dollar_volume_20",
            "dollar_volume_ratio_20",
        ):
            checked.loc[positions, feature] = context[feature].reindex(
                dates
            ).to_numpy()

    volatility = features["realized_vol_63"].copy()
    volatility_change = (
        volatility.groupby(level="ticker", sort=False)
        .transform(lambda values: values / values.shift(20) - 1.0)
    )
    checked["realized_volatility_change_20"] = volatility_change.reindex(
        keys
    ).to_numpy()

    terminal = checked["actual_terminal_return"].to_numpy(dtype=float)
    path = checked["actual_path_mae"].to_numpy(dtype=float)
    checked["outcome_state"] = np.select(
        (
            terminal <= TERMINAL_DOWN_THRESHOLD,
            terminal >= EXTREME_UP_THRESHOLD,
            path <= PATH_STRESS_THRESHOLD,
        ),
        (
            "terminal_down",
            "extreme_up",
            "path_only_stress",
        ),
        default="other",
    )
    checked["earnings_proximity_status"] = "unavailable"
    checked["market_cap_status"] = "unavailable"
    ordered = (
        "ticker",
        "observation_date",
        "fold",
        "group",
        "regime",
        "calibrated_down_probability",
        "actual_terminal_return",
        "actual_path_mae",
        "outcome_state",
        *AUDIT_FEATURE_TYPES,
        "earnings_proximity_status",
        "market_cap_status",
    )
    return checked.loc[:, ordered].sort_values(
        ["fold", "group", "regime", "observation_date", "ticker"],
        kind="mergesort",
    ).reset_index(drop=True)


def match_extreme_up_to_terminal_down(
    population: pd.DataFrame,
    *,
    maximum_calendar_days: int = 63,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Greedily match extreme-up cases to unique exact-stratum down controls."""
    checked = _validated_population(population)
    if (
        isinstance(maximum_calendar_days, bool)
        or not isinstance(maximum_calendar_days, int)
        or maximum_calendar_days < 1
    ):
        raise ValueError("maximum_calendar_days must be a positive integer")
    cases = checked.loc[
        checked["outcome_state"] == "extreme_up"
    ].sort_values(
        ["fold", "group", "regime", "observation_date", "ticker"],
        kind="mergesort",
    )
    controls = checked.loc[
        checked["outcome_state"] == "terminal_down"
    ].copy()
    unused_controls = set(controls.index)
    pair_rows = []
    unmatched = []
    for case_index, case in cases.iterrows():
        exact = controls.loc[
            (controls["fold"] == case["fold"])
            & (controls["group"] == case["group"])
            & (controls["regime"] == case["regime"])
        ].copy()
        if exact.empty:
            unmatched.append((case_index, "no_exact_stratum_control"))
            continue
        exact["_date_distance"] = (
            exact["observation_date"] - case["observation_date"]
        ).abs().dt.days
        bounded = exact.loc[
            exact["_date_distance"] <= maximum_calendar_days
        ].copy()
        if bounded.empty:
            unmatched.append((case_index, "outside_date_window"))
            continue
        bounded = bounded.loc[bounded.index.isin(unused_controls)].copy()
        if bounded.empty:
            unmatched.append((case_index, "control_exhausted"))
            continue
        case_volatility = _finite_or_none(case["realized_vol_63"])
        control_volatility = pd.to_numeric(
            bounded["realized_vol_63"],
            errors="coerce",
        )
        if case_volatility is None:
            bounded["_volatility_distance"] = np.inf
        else:
            bounded["_volatility_distance"] = (
                control_volatility - case_volatility
            ).abs()
            bounded["_volatility_distance"] = bounded[
                "_volatility_distance"
            ].where(control_volatility.notna(), np.inf)
        bounded = bounded.sort_values(
            [
                "_date_distance",
                "_volatility_distance",
                "observation_date",
                "ticker",
            ],
            kind="mergesort",
        )
        control_index = bounded.index[0]
        control = controls.loc[control_index]
        unused_controls.remove(control_index)
        pair_rows.append(
            _pair_record(
                len(pair_rows) + 1,
                case,
                control,
                date_distance=int(bounded.iloc[0]["_date_distance"]),
                volatility_distance=bounded.iloc[0][
                    "_volatility_distance"
                ],
            )
        )
    pairs = pd.DataFrame(pair_rows, columns=_pair_columns())
    coverage = _matching_coverage(cases, pairs, unmatched)
    return pairs, coverage


def _validated_predictions(predictions):
    if not isinstance(predictions, pd.DataFrame):
        raise TypeError("predictions must be a DataFrame")
    required = (
        "ticker",
        "observation_date",
        "fold",
        "group",
        "regime",
        "calibrated_down_probability",
        "actual_terminal_return",
        "actual_path_mae",
    )
    missing = [column for column in required if column not in predictions]
    if missing:
        raise ValueError(f"predictions are missing columns: {missing}")
    checked = predictions.loc[:, required].copy(deep=True)
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    if checked.duplicated(["ticker", "observation_date"]).any():
        raise ValueError("predictions contain duplicate ticker/date keys")
    for column in (
        "calibrated_down_probability",
        "actual_terminal_return",
        "actual_path_mae",
    ):
        checked[column] = pd.to_numeric(checked[column], errors="coerce")
    finite = np.isfinite(
        checked[
            [
                "calibrated_down_probability",
                "actual_terminal_return",
                "actual_path_mae",
            ]
        ].to_numpy(dtype=float)
    ).all(axis=1)
    return checked.loc[finite].copy()


def _validated_feature_frame(feature_frame):
    if not isinstance(feature_frame, pd.DataFrame):
        raise TypeError("feature_frame must be a DataFrame")
    if list(feature_frame.index.names) != [
        "ticker",
        "observation_date",
    ]:
        raise ValueError("feature_frame must use ticker/date MultiIndex")
    if feature_frame.index.has_duplicates:
        raise ValueError("feature_frame contains duplicate keys")
    checked = feature_frame.copy(deep=True)
    tickers = checked.index.get_level_values("ticker").astype(str).str.upper()
    dates = pd.DatetimeIndex(
        pd.to_datetime(
            checked.index.get_level_values("observation_date"),
            errors="raise",
        )
    ).tz_localize(None)
    checked.index = pd.MultiIndex.from_arrays(
        (tickers, dates),
        names=("ticker", "observation_date"),
    )
    return checked.sort_index()


def _history_context(history):
    required = ("Open", "Close", "Volume")
    missing = [column for column in required if column not in history]
    if missing:
        raise ValueError(f"history is missing columns: {missing}")
    ordered = history.loc[:, required].copy(deep=True).sort_index()
    ordered.index = pd.DatetimeIndex(
        pd.to_datetime(ordered.index, errors="raise")
    ).tz_localize(None)
    if ordered.index.has_duplicates:
        raise ValueError("history contains duplicate dates")
    open_price = pd.to_numeric(ordered["Open"], errors="coerce")
    close = pd.to_numeric(ordered["Close"], errors="coerce")
    volume = pd.to_numeric(ordered["Volume"], errors="coerce")
    dollar_volume = close * volume
    median_dollar_volume = dollar_volume.rolling(
        20,
        min_periods=20,
    ).median()
    return pd.DataFrame(
        {
            "opening_gap": open_price / close.shift(1).replace(0.0, np.nan)
            - 1.0,
            "log_dollar_volume_20": np.log(
                median_dollar_volume.where(median_dollar_volume > 0.0)
            ),
            "dollar_volume_ratio_20": dollar_volume
            / median_dollar_volume.replace(0.0, np.nan),
        },
        index=ordered.index,
    )


def _empty_population(predictions):
    columns = (
        "ticker",
        "observation_date",
        "fold",
        "group",
        "regime",
        "calibrated_down_probability",
        "actual_terminal_return",
        "actual_path_mae",
        "outcome_state",
        *AUDIT_FEATURE_TYPES,
        "earnings_proximity_status",
        "market_cap_status",
    )
    return pd.DataFrame(columns=columns)


def _validated_population(population):
    if not isinstance(population, pd.DataFrame):
        raise TypeError("population must be a DataFrame")
    required = (
        "ticker",
        "observation_date",
        "fold",
        "group",
        "regime",
        "outcome_state",
        *AUDIT_FEATURE_TYPES,
    )
    missing = [column for column in required if column not in population]
    if missing:
        raise ValueError(f"population is missing columns: {missing}")
    checked = population.loc[:, required].copy(deep=True)
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    if checked.duplicated(["ticker", "observation_date"]).any():
        raise ValueError("population contains duplicate ticker/date keys")
    allowed = {
        "terminal_down",
        "extreme_up",
        "path_only_stress",
        "other",
    }
    if not set(checked["outcome_state"]).issubset(allowed):
        raise ValueError("population contains unknown outcome state")
    return checked


def _pair_record(
    pair_id,
    case,
    control,
    *,
    date_distance,
    volatility_distance,
):
    record = {
        "pair_id": int(pair_id),
        "case_key": _row_key(case),
        "control_key": _row_key(control),
        "case_ticker": case["ticker"],
        "case_observation_date": case["observation_date"],
        "control_ticker": control["ticker"],
        "control_observation_date": control["observation_date"],
        "fold": int(case["fold"]),
        "group": str(case["group"]),
        "regime": str(case["regime"]),
        "calendar_distance_days": int(date_distance),
        "realized_volatility_distance": (
            float(volatility_distance)
            if np.isfinite(volatility_distance)
            else np.nan
        ),
    }
    for feature in AUDIT_FEATURE_TYPES:
        record[f"case_{feature}"] = case[feature]
        record[f"control_{feature}"] = control[feature]
    return record


def _pair_columns():
    return (
        "pair_id",
        "case_key",
        "control_key",
        "case_ticker",
        "case_observation_date",
        "control_ticker",
        "control_observation_date",
        "fold",
        "group",
        "regime",
        "calendar_distance_days",
        "realized_volatility_distance",
        *tuple(
            column
            for feature in AUDIT_FEATURE_TYPES
            for column in (f"case_{feature}", f"control_{feature}")
        ),
    )


def _row_key(row):
    return (
        f"{row['ticker']}|"
        f"{pd.Timestamp(row['observation_date']).date().isoformat()}"
    )


def _matching_coverage(cases, pairs, unmatched):
    matched_keys = set(pairs["case_key"]) if not pairs.empty else set()
    case_rows = cases.copy()
    case_rows["_matched"] = case_rows.apply(
        lambda row: _row_key(row) in matched_keys,
        axis=1,
    )
    rows = [
        _coverage_row(
            case_rows,
            scope_type="overall",
            scope_name="all",
        )
    ]
    for scope_type, column in (
        ("fold", "fold"),
        ("group", "group"),
        ("regime", "regime"),
    ):
        for name, selected in case_rows.groupby(column, sort=True):
            rows.append(
                _coverage_row(
                    selected,
                    scope_type=scope_type,
                    scope_name=str(name),
                )
            )
    result = pd.DataFrame(rows)
    result["unmatched_reason_count"] = 0
    if unmatched:
        result.loc[
            result["scope_type"] == "overall",
            "unmatched_reason_count",
        ] = len(unmatched)
    return result


def _coverage_row(frame, *, scope_type, scope_name):
    case_count = len(frame)
    matched = int(frame["_matched"].sum()) if case_count else 0
    return {
        "scope_type": scope_type,
        "scope_name": scope_name,
        "case_count": int(case_count),
        "matched_pair_count": matched,
        "unmatched_case_count": int(case_count - matched),
        "match_rate": matched / case_count if case_count else 0.0,
    }


def _finite_or_none(value):
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if np.isfinite(converted) else None
