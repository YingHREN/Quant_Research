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


def paired_feature_evidence(
    pairs: pd.DataFrame,
    *,
    feature_types: Mapping[str, str],
    bootstrap_samples: int = 2_000,
    bootstrap_block_days: int = 20,
    seed: int = 20260730,
) -> pd.DataFrame:
    """Summarize paired feature differences with deterministic date blocks."""
    checked, registry = _validated_evidence_inputs(
        pairs,
        feature_types,
        bootstrap_samples=bootstrap_samples,
        bootstrap_block_days=bootstrap_block_days,
        seed=seed,
    )
    rows = []
    for feature, feature_type in registry.items():
        case = _feature_values(
            checked[f"case_{feature}"],
            feature_type=feature_type,
        )
        control = _feature_values(
            checked[f"control_{feature}"],
            feature_type=feature_type,
        )
        both = case.notna() & control.notna()
        differences = case.loc[both] - control.loc[both]
        status = "available"
        standardized = np.nan
        paired_mean = np.nan
        paired_median = np.nan
        ci_low = np.nan
        ci_high = np.nan
        raw_p_value = np.nan
        if len(differences):
            paired_mean = float(differences.mean())
            paired_median = float(differences.median())
        if len(differences) < 2:
            status = "insufficient_pairs"
        else:
            scale = float(differences.std(ddof=1))
            if not np.isfinite(scale) or scale <= 0.0:
                status = "effect_unavailable"
            else:
                standardized = paired_mean / scale
                replicates = _date_block_bootstrap(
                    differences,
                    checked.loc[both, "case_observation_date"],
                    samples=bootstrap_samples,
                    block_days=bootstrap_block_days,
                    seed=seed,
                )
                ci_low, ci_high = np.quantile(
                    replicates,
                    (0.025, 0.975),
                )
                raw_p_value = min(
                    1.0,
                    2.0
                    * min(
                        float((replicates <= 0.0).mean()),
                        float((replicates >= 0.0).mean()),
                    ),
                )
        direction = (
            int(np.sign(standardized))
            if np.isfinite(standardized) and standardized != 0.0
            else 0
        )
        rows.append(
            {
                "feature": feature,
                "feature_type": feature_type,
                "provenance": "observation_date_causal",
                "pair_count": int(len(checked)),
                "case_available_count": int(case.notna().sum()),
                "control_available_count": int(control.notna().sum()),
                "both_available_count": int(both.sum()),
                "case_availability": (
                    float(case.notna().mean()) if len(checked) else 0.0
                ),
                "control_availability": (
                    float(control.notna().mean()) if len(checked) else 0.0
                ),
                "case_median": _finite_or_nan(case.median()),
                "control_median": _finite_or_nan(control.median()),
                "paired_mean_difference": paired_mean,
                "paired_median_difference": paired_median,
                "standardized_difference": standardized,
                "ci_low": _finite_or_nan(ci_low),
                "ci_high": _finite_or_nan(ci_high),
                "raw_p_value": _finite_or_nan(raw_p_value),
                "adjusted_p_value": np.nan,
                "consistent_folds": _consistent_slice_count(
                    checked.loc[both],
                    differences,
                    column="fold",
                    direction=direction,
                ),
                "consistent_large_groups": _consistent_group_count(
                    checked.loc[both],
                    differences,
                    direction=direction,
                ),
                "status": status,
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["adjusted_p_value"] = _benjamini_hochberg(
        result["raw_p_value"]
    )
    gate_reasons = [
        _admission_reasons(row)
        for row in result.to_dict(orient="records")
    ]
    result["gate_reasons"] = gate_reasons
    result["gate_passed"] = [not reasons for reasons in gate_reasons]
    return result.sort_values("feature", kind="mergesort").reset_index(
        drop=True
    )


def admitted_feature_hypotheses(evidence: pd.DataFrame) -> tuple[str, ...]:
    """Return features satisfying every frozen point-in-time admission gate."""
    if not isinstance(evidence, pd.DataFrame):
        raise TypeError("evidence must be a DataFrame")
    required = (
        "feature",
        "pair_count",
        "case_availability",
        "control_availability",
        "standardized_difference",
        "ci_low",
        "ci_high",
        "consistent_folds",
        "consistent_large_groups",
        "provenance",
    )
    missing = [column for column in required if column not in evidence]
    if missing:
        raise ValueError(f"evidence is missing columns: {missing}")
    admitted = []
    for row in evidence.loc[:, required].to_dict(orient="records"):
        if not _admission_reasons(row):
            admitted.append(str(row["feature"]))
    return tuple(sorted(set(admitted)))


def _admission_reasons(row):
    numeric_fields = (
        "pair_count",
        "case_availability",
        "control_availability",
        "standardized_difference",
        "ci_low",
        "ci_high",
        "consistent_folds",
        "consistent_large_groups",
    )
    if not all(
        _finite_or_none(row.get(field)) is not None
        for field in numeric_fields
    ):
        return ("effect_unavailable",)
    reasons = []
    if row.get("status", "available") != "available":
        reasons.append("effect_unavailable")
    if str(row.get("provenance")) != "observation_date_causal":
        reasons.append("non_causal_provenance")
    if float(row["pair_count"]) < 1_000:
        reasons.append("insufficient_matched_pairs")
    if float(row["case_availability"]) < 0.90:
        reasons.append("case_availability_below_gate")
    if float(row["control_availability"]) < 0.90:
        reasons.append("control_availability_below_gate")
    if abs(float(row["standardized_difference"])) < 0.20:
        reasons.append("effect_size_below_gate")
    if not (
        float(row["ci_low"]) > 0.0 or float(row["ci_high"]) < 0.0
    ):
        reasons.append("confidence_interval_crosses_zero")
    if float(row["consistent_folds"]) < 4:
        reasons.append("fold_stability_below_gate")
    if float(row["consistent_large_groups"]) < 2:
        reasons.append("group_stability_below_gate")
    return tuple(reasons)


def _validated_evidence_inputs(
    pairs,
    feature_types,
    *,
    bootstrap_samples,
    bootstrap_block_days,
    seed,
):
    if not isinstance(pairs, pd.DataFrame):
        raise TypeError("pairs must be a DataFrame")
    if not isinstance(feature_types, Mapping) or not feature_types:
        raise ValueError("feature_types must be a non-empty mapping")
    registry = OrderedDict()
    for feature, feature_type in feature_types.items():
        normalized_feature = str(feature).strip()
        normalized_type = str(feature_type).strip()
        if not normalized_feature or normalized_type not in {
            "numeric",
            "boolean",
        }:
            raise ValueError("feature registry is invalid")
        registry[normalized_feature] = normalized_type
    required = (
        "case_observation_date",
        "fold",
        "group",
        *tuple(
            column
            for feature in registry
            for column in (f"case_{feature}", f"control_{feature}")
        ),
    )
    missing = [column for column in required if column not in pairs]
    if missing:
        raise ValueError(f"pairs are missing columns: {missing}")
    for value, name in (
        (bootstrap_samples, "bootstrap_samples"),
        (bootstrap_block_days, "bootstrap_block_days"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise ValueError(f"{name} must be an integer of at least two")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    checked = pairs.loc[:, required].copy(deep=True)
    checked["case_observation_date"] = pd.to_datetime(
        checked["case_observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    return checked, registry


def _feature_values(values, *, feature_type):
    result = pd.to_numeric(values, errors="coerce").astype(float)
    finite = np.isfinite(result.to_numpy())
    result = result.where(finite)
    if feature_type == "boolean":
        invalid = result.notna() & ~result.isin((0.0, 1.0))
        if invalid.any():
            raise ValueError("boolean audit feature must contain only 0/1")
    return result


def _date_block_bootstrap(
    differences,
    dates,
    *,
    samples,
    block_days,
    seed,
):
    aligned = pd.DataFrame(
        {
            "difference": np.asarray(differences, dtype=float),
            "date": pd.to_datetime(dates).to_numpy(),
        }
    ).sort_values("date", kind="mergesort")
    unique_dates = pd.Index(aligned["date"].unique()).sort_values()
    date_positions = {
        date: position for position, date in enumerate(unique_dates)
    }
    aligned["_block"] = aligned["date"].map(
        lambda date: date_positions[date] // block_days
    )
    blocks = tuple(
        selected["difference"].to_numpy(dtype=float)
        for _, selected in aligned.groupby("_block", sort=True)
    )
    rng = np.random.default_rng(seed)
    result = np.empty(samples, dtype=float)
    for index in range(samples):
        chosen = rng.integers(0, len(blocks), size=len(blocks))
        values = np.concatenate([blocks[position] for position in chosen])
        result[index] = values.mean()
    return result


def _consistent_slice_count(frame, differences, *, column, direction):
    if direction == 0 or frame.empty:
        return 0
    aligned = frame.loc[:, [column]].copy()
    aligned["_difference"] = np.asarray(differences, dtype=float)
    return int(
        sum(
            np.sign(selected["_difference"].mean()) == direction
            for _, selected in aligned.groupby(column, sort=True)
        )
    )


def _consistent_group_count(frame, differences, *, direction):
    if direction == 0 or frame.empty:
        return 0
    aligned = frame.loc[:, ["group"]].copy()
    aligned["_difference"] = np.asarray(differences, dtype=float)
    count = 0
    for group in ("semiconductor", "software", "other"):
        selected = aligned.loc[aligned["group"] == group, "_difference"]
        if len(selected) and np.sign(selected.mean()) == direction:
            count += 1
    return count


def _benjamini_hochberg(values):
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    result = np.full(len(numeric), np.nan, dtype=float)
    finite_positions = np.flatnonzero(np.isfinite(numeric))
    if not len(finite_positions):
        return result
    order = finite_positions[
        np.argsort(numeric[finite_positions], kind="mergesort")
    ]
    total = len(order)
    adjusted = np.minimum(
        numeric[order] * total / np.arange(1, total + 1),
        1.0,
    )
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    result[order] = adjusted
    return result


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


def _finite_or_nan(value):
    converted = _finite_or_none(value)
    return converted if converted is not None else np.nan
