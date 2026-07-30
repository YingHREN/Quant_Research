"""Leakage-safe event labels and metrics for causal bottom states."""

from __future__ import annotations

from bisect import bisect_left
import math

import numpy as np
import pandas as pd

from research.bottom_state import POSITIVE_STATES, STATE_RANK


HORIZONS = (5, 10, 20)
STRUCTURE_STATES = frozenset(
    ("bullish_structure_confirmed", "breakout_retest_confirmed")
)
TERMINAL_FAILURE = "bottom_failed"
REQUIRED_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
REQUIRED_STATE_COLUMNS = (
    "bottom_state",
    "bottom_state_transition",
    "bottom_score",
    "bottom_coverage",
    "bottom_state_age_sessions",
)
OUTPUT_COLUMNS = (
    "event_id",
    "ticker",
    "observation_date",
    "event_end_date",
    "observation_state",
    "observation_rank",
    "event_role",
    "scope",
    "horizon",
    "observation_close",
    "drawdown_63",
    "drawdown_bin",
    "forward_return",
    "positive_return",
    "maximum_favorable_excursion",
    "maximum_adverse_excursion",
    "confirmed_within_horizon",
    "failed_within_horizon",
    "first_terminal_state",
    "sessions_to_confirmation",
    "sessions_to_failure",
    "state_maintained",
    "bottom_score",
    "bottom_coverage",
    "bottom_state_age_sessions",
)
MATCH_KEY_COLUMNS = (
    "cohort",
    "fold",
    "market_regime",
    "horizon",
    "scope",
    "variant",
)
MATCH_REQUIRED_COLUMNS = (
    "event_id",
    "ticker",
    "observation_date",
    "observation_state",
    "event_role",
    "drawdown_bin",
    "group",
) + MATCH_KEY_COLUMNS
DRAWNDOWN_BIN_ORDER = (
    "0_-15",
    "-15_-25",
    "-25_-40",
    "below_-40",
)
PAIR_COLUMNS = (
    "pair_id",
    "event_id",
    "baseline_event_id",
    "ticker",
    "baseline_ticker",
    "observation_date",
    "baseline_observation_date",
    "observation_state",
    "cohort",
    "fold",
    "group",
    "market_regime",
    "horizon",
    "scope",
    "variant",
    "drawdown_bin",
    "baseline_drawdown_bin",
    "match_tier",
    "matched",
)
OUTCOME_COLUMNS = (
    "forward_return",
    "positive_return",
    "maximum_favorable_excursion",
    "maximum_adverse_excursion",
    "confirmed_within_horizon",
    "failed_within_horizon",
    "sessions_to_confirmation",
    "sessions_to_failure",
    "state_maintained",
)


def build_bottom_transition_events(
    ticker: str,
    history: pd.DataFrame,
    states: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = HORIZONS,
    non_overlap_sessions: int = 20,
) -> pd.DataFrame:
    """Return mature bottom-state outcomes for both event scopes."""
    checked_horizons = _validate_inputs(
        history,
        states,
        horizons=horizons,
        non_overlap_sessions=non_overlap_sessions,
    )
    frame = history.loc[:, REQUIRED_PRICE_COLUMNS].astype(float)
    state_frame = states.reindex(frame.index)
    normalized_ticker = str(ticker).strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker must be non-empty")
    drawdown = frame["Close"] / frame["Close"].rolling(63).max() - 1.0
    candidates = _candidate_positions(state_frame)
    scope_positions = {
        "all_transitions": candidates,
        "non_overlapping": _non_overlapping_positions(
            candidates,
            state_frame,
            non_overlap_sessions=non_overlap_sessions,
        ),
    }
    rows = []
    for scope, positions in scope_positions.items():
        for position in positions:
            for horizon in checked_horizons:
                if position + horizon >= len(frame):
                    continue
                rows.append(
                    _event_row(
                        normalized_ticker,
                        frame,
                        state_frame,
                        drawdown,
                        position=position,
                        horizon=horizon,
                        scope=scope,
                    )
                )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def match_downtrend_baselines(events: pd.DataFrame) -> pd.DataFrame:
    """Return deterministic one-to-one positive-event/baseline pairs."""
    _validate_matching_events(events)
    if events.empty:
        return _empty_pairs()
    source = events.copy(deep=True)
    source["observation_date"] = pd.to_datetime(
        source["observation_date"],
        errors="raise",
    )
    positives = source.loc[
        source["event_role"].eq("event")
        & source["observation_state"].isin(POSITIVE_STATES)
        & source["drawdown_bin"].isin(DRAWNDOWN_BIN_ORDER)
    ].copy()
    baselines = source.loc[
        source["event_role"].eq("baseline")
        & source["observation_state"].eq("downtrend_continuation")
        & source["drawdown_bin"].isin(DRAWNDOWN_BIN_ORDER)
    ].copy()
    if positives.empty or baselines.empty:
        return _empty_pairs()
    positives = positives.sort_values(
        list(MATCH_KEY_COLUMNS)
        + ["observation_date", "ticker", "event_id"],
        kind="stable",
    )
    baseline_rows = {
        str(row["event_id"]): row
        for _, row in baselines.iterrows()
    }
    ticker_index = _baseline_index(
        baselines,
        identity_column="ticker",
    )
    group_index = _baseline_index(
        baselines,
        identity_column="group",
    )
    used_baselines: set[str] = set()
    pairs = []
    for _, event in positives.iterrows():
        match_keys = tuple(event[column] for column in MATCH_KEY_COLUMNS)
        drawdown_bin = str(event["drawdown_bin"])
        adjacent_bins = _adjacent_drawdown_bins(drawdown_bin)
        tiers = (
            (
                "same_ticker_exact_bin",
                ticker_index,
                ((match_keys, event["ticker"], drawdown_bin),),
            ),
            (
                "same_ticker_adjacent_bin",
                ticker_index,
                tuple(
                    (match_keys, event["ticker"], value)
                    for value in adjacent_bins
                ),
            ),
            (
                "same_group_exact_bin",
                group_index,
                ((match_keys, event["group"], drawdown_bin),),
            ),
            (
                "same_group_adjacent_bin",
                group_index,
                tuple(
                    (match_keys, event["group"], value)
                    for value in adjacent_bins
                ),
            ),
        )
        selected_id = None
        selected_tier = None
        for tier, index, keys in tiers:
            selected_id = _nearest_available_baseline(
                index,
                keys,
                pd.Timestamp(event["observation_date"]),
                used_baselines,
                baseline_rows,
            )
            if selected_id is not None:
                selected_tier = tier
                break
        if selected_id is None:
            continue
        used_baselines.add(selected_id)
        pair = _pair_row(
            event,
            baseline_rows[selected_id],
            match_tier=str(selected_tier),
        )
        pairs.append(pair)
    columns = list(PAIR_COLUMNS) + [
        f"{side}_{column}"
        for side in ("event", "baseline")
        for column in OUTCOME_COLUMNS
    ]
    return pd.DataFrame(pairs, columns=columns)


def evaluate_bottom_events(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate unmatched and matched bottom-state outcome metrics."""
    _validate_matching_events(events)
    missing_outcomes = [
        column for column in OUTCOME_COLUMNS if column not in events
    ]
    if missing_outcomes:
        raise ValueError(
            f"events are missing outcome columns: {missing_outcomes}"
        )
    positives = events.loc[
        events["event_role"].eq("event")
        & events["observation_state"].isin(POSITIVE_STATES)
    ].copy()
    pairs = match_downtrend_baselines(events)
    rows = []
    if positives.empty:
        return pd.DataFrame(columns=_metric_columns())
    group_keys = ("cohort", "variant", "horizon", "scope")
    for key, group in positives.groupby(list(group_keys), sort=True):
        key_values = dict(zip(group_keys, key))
        state_slices = _state_slices(group)
        for state_slice, state_rows in state_slices:
            for slice_dimension, slice_value, sliced in _event_slices(
                state_rows
            ):
                matched = pairs.loc[
                    pairs["event_id"].isin(sliced["event_id"])
                ]
                common = {
                    **key_values,
                    "state_slice": state_slice,
                    "slice_dimension": slice_dimension,
                    "slice_value": str(slice_value),
                }
                rows.append(
                    _metric_row(
                        common,
                        sliced,
                        matched,
                        metric_scope="all_events",
                    )
                )
                if not matched.empty:
                    rows.append(
                        _metric_row(
                            common,
                            sliced,
                            matched,
                            metric_scope="matched",
                        )
                    )
    return pd.DataFrame(rows, columns=_metric_columns()).sort_values(
        [
            "cohort",
            "variant",
            "horizon",
            "scope",
            "state_slice",
            "slice_dimension",
            "slice_value",
            "metric_scope",
        ],
        kind="stable",
    ).reset_index(drop=True)


def bottom_evaluation_decision(
    metrics: pd.DataFrame,
    *,
    evidence_contract_passed: bool,
    group_causal_audit_passed: bool,
    future_holdout_passed: bool,
) -> dict[str, object]:
    """Return a fail-closed advisory-only research decision."""
    required = {
        "cohort",
        "variant",
        "horizon",
        "scope",
        "state_slice",
        "slice_dimension",
        "slice_value",
        "metric_scope",
        "matched_count",
        "positive_rate_gain",
        "return_gain",
        "mae_delta",
        "confirmation_rate",
    }
    available = isinstance(metrics, pd.DataFrame) and required.issubset(
        metrics.columns
    )
    target_base = (
        metrics.loc[
            metrics["cohort"].eq("confirmation")
            & metrics["variant"].eq("full")
            & metrics["horizon"].eq(10)
            & metrics["scope"].eq("non_overlapping")
            & metrics["metric_scope"].eq("matched")
        ].copy()
        if available
        else pd.DataFrame()
    )
    target = target_base.loc[
        target_base["state_slice"].eq("early_states")
    ]
    overall = target.loc[
        target["slice_dimension"].eq("all")
        & target["slice_value"].astype(str).eq("all")
    ]
    overall_row = overall.iloc[0] if len(overall) == 1 else None
    positive_gain = _row_at_least(
        overall_row,
        "positive_rate_gain",
        0.05,
    )
    return_gain = _row_at_least(overall_row, "return_gain", 0.02)
    mae_nonworse = _row_at_least(overall_row, "mae_delta", 0.0)
    fold_rows = target.loc[target["slice_dimension"].eq("fold")]
    fold_wins = int(
        (
            pd.to_numeric(
                fold_rows["positive_rate_gain"],
                errors="coerce",
            ).gt(0.0)
            & pd.to_numeric(
                fold_rows["return_gain"],
                errors="coerce",
            ).gt(0.0)
        ).sum()
    )
    group_rows = target.loc[target["slice_dimension"].eq("group")]
    group_wins = int(
        (
            pd.to_numeric(
                group_rows["matched_count"],
                errors="coerce",
            ).ge(100)
            & pd.to_numeric(
                group_rows["positive_rate_gain"],
                errors="coerce",
            ).gt(0.0)
            & pd.to_numeric(
                group_rows["return_gain"],
                errors="coerce",
            ).gt(0.0)
        ).sum()
    )
    group_slices_present = not group_rows.empty
    drawdown_slices_present = bool(
        target["slice_dimension"].eq("drawdown_bin").any()
    )
    stage_monotonic = _stage_confirmation_monotonic(target_base)
    ablations_beaten = _ablations_beaten(metrics, overall_row) if available else 0
    conditions = {
        "positive_rate_gain_at_least_5pp": positive_gain,
        "mean_return_gain_at_least_2pp": return_gain,
        "mae_not_worse": mae_nonworse,
        "at_least_3_fold_wins": fold_wins >= 3,
        "at_least_2_large_group_wins": group_wins >= 2,
        "stage_confirmation_monotonic": stage_monotonic,
        "full_beats_at_least_3_ablations": ablations_beaten >= 3,
        "group_slices_present": group_slices_present,
        "drawdown_slices_present": drawdown_slices_present,
        "evidence_contract_passed": bool(evidence_contract_passed),
        "group_causal_audit_passed": bool(group_causal_audit_passed),
        "future_holdout_passed": bool(future_holdout_passed),
    }
    reason_map = (
        ("metrics_missing", available),
        ("positive_rate_gain_below_5pp", positive_gain),
        ("mean_return_gain_below_2pp", return_gain),
        ("mae_worsened", mae_nonworse),
        ("insufficient_fold_wins", fold_wins >= 3),
        ("insufficient_group_evidence", group_wins >= 2),
        ("stage_monotonicity_failed", stage_monotonic),
        ("insufficient_ablation_advantage", ablations_beaten >= 3),
        ("group_slices_missing", group_slices_present),
        ("drawdown_slices_missing", drawdown_slices_present),
        ("evidence_contract_failed", bool(evidence_contract_passed)),
        ("group_causal_audit_failed", bool(group_causal_audit_passed)),
        ("future_holdout_required", bool(future_holdout_passed)),
    )
    reasons = [reason for reason, passed in reason_map if not passed]
    return {
        "eligible": not reasons,
        "authority": "advisory_only",
        "reasons": reasons,
        "performance_conditions": conditions,
        "fold_wins": fold_wins,
        "group_wins": group_wins,
        "ablations_beaten": ablations_beaten,
    }


def _metric_columns() -> list[str]:
    return [
        "cohort",
        "variant",
        "horizon",
        "scope",
        "state_slice",
        "slice_dimension",
        "slice_value",
        "metric_scope",
        "event_count",
        "matched_count",
        "match_coverage",
        "mean_return",
        "median_return",
        "positive_rate",
        "mean_mfe",
        "mean_mae",
        "confirmation_rate",
        "failure_rate",
        "mean_sessions_to_confirmation",
        "mean_sessions_to_failure",
        "maintenance_rate",
        "annualized_event_frequency",
        "baseline_mean_return",
        "baseline_median_return",
        "baseline_positive_rate",
        "baseline_mean_mfe",
        "baseline_mean_mae",
        "baseline_confirmation_rate",
        "baseline_failure_rate",
        "baseline_maintenance_rate",
        "return_gain",
        "positive_rate_gain",
        "mfe_gain",
        "mae_delta",
    ]


def _state_slices(
    events: pd.DataFrame,
) -> list[tuple[str, pd.DataFrame]]:
    slices = [
        (state, events.loc[events["observation_state"].eq(state)])
        for state in POSITIVE_STATES
    ]
    early = events.loc[
        events["observation_state"].isin(
            ("seller_exhaustion_watch", "early_bullish_reversal_watch")
        )
    ]
    structure = events.loc[
        events["observation_state"].isin(STRUCTURE_STATES)
    ]
    slices.extend(
        (
            ("early_states", early),
            ("structure_confirmed", structure),
            ("all_positive", events),
        )
    )
    return [(name, frame) for name, frame in slices if not frame.empty]


def _event_slices(events: pd.DataFrame):
    yield "all", "all", events
    for dimension, column in (
        ("fold", "fold"),
        ("group", "group"),
        ("market_regime", "market_regime"),
        ("drawdown_bin", "drawdown_bin"),
    ):
        for value, sliced in events.groupby(column, sort=True, dropna=False):
            yield dimension, value, sliced


def _metric_row(
    common: dict[str, object],
    all_events: pd.DataFrame,
    pairs: pd.DataFrame,
    *,
    metric_scope: str,
) -> dict[str, object]:
    matched_count = len(pairs)
    if metric_scope == "matched":
        event_values = {
            column: pairs[f"event_{column}"] for column in OUTCOME_COLUMNS
        }
    else:
        event_values = {
            column: all_events[column] for column in OUTCOME_COLUMNS
        }
    baseline_values = {
        column: pairs[f"baseline_{column}"] for column in OUTCOME_COLUMNS
    }
    event_metrics = _outcome_metrics(event_values)
    baseline_metrics = (
        _outcome_metrics(baseline_values)
        if matched_count
        else _empty_outcome_metrics()
    )
    event_count = len(all_events)
    years = _observation_years(all_events["observation_date"])
    ticker_count = max(1, all_events["ticker"].nunique())
    row = {
        **common,
        "metric_scope": metric_scope,
        "event_count": event_count,
        "matched_count": matched_count,
        "match_coverage": matched_count / event_count if event_count else 0.0,
        **event_metrics,
        "annualized_event_frequency": event_count / ticker_count / years,
        **{
            f"baseline_{key}": value
            for key, value in baseline_metrics.items()
            if key
            in {
                "mean_return",
                "median_return",
                "positive_rate",
                "mean_mfe",
                "mean_mae",
                "confirmation_rate",
                "failure_rate",
                "maintenance_rate",
            }
        },
    }
    row["return_gain"] = _difference(
        row.get("mean_return"),
        row.get("baseline_mean_return"),
    )
    row["positive_rate_gain"] = _difference(
        row.get("positive_rate"),
        row.get("baseline_positive_rate"),
    )
    row["mfe_gain"] = _difference(
        row.get("mean_mfe"),
        row.get("baseline_mean_mfe"),
    )
    row["mae_delta"] = _difference(
        row.get("mean_mae"),
        row.get("baseline_mean_mae"),
    )
    return row


def _outcome_metrics(values: dict[str, pd.Series]) -> dict[str, float]:
    return {
        "mean_return": _series_mean(values["forward_return"]),
        "median_return": _series_median(values["forward_return"]),
        "positive_rate": _series_mean(values["positive_return"]),
        "mean_mfe": _series_mean(values["maximum_favorable_excursion"]),
        "mean_mae": _series_mean(values["maximum_adverse_excursion"]),
        "confirmation_rate": _series_mean(
            values["confirmed_within_horizon"]
        ),
        "failure_rate": _series_mean(values["failed_within_horizon"]),
        "mean_sessions_to_confirmation": _series_mean(
            values["sessions_to_confirmation"]
        ),
        "mean_sessions_to_failure": _series_mean(
            values["sessions_to_failure"]
        ),
        "maintenance_rate": _series_mean(values["state_maintained"]),
    }


def _empty_outcome_metrics() -> dict[str, float]:
    return {
        key: math.nan
        for key in (
            "mean_return",
            "median_return",
            "positive_rate",
            "mean_mfe",
            "mean_mae",
            "confirmation_rate",
            "failure_rate",
            "mean_sessions_to_confirmation",
            "mean_sessions_to_failure",
            "maintenance_rate",
        )
    }


def _series_mean(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    return float(numeric.mean()) if numeric.notna().any() else math.nan


def _series_median(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    return float(numeric.median()) if numeric.notna().any() else math.nan


def _observation_years(values: pd.Series) -> float:
    dates = pd.to_datetime(values, errors="coerce").dropna()
    if dates.empty:
        return 1.0
    span_days = max(1, int((dates.max() - dates.min()).days) + 1)
    return max(1.0, span_days / 365.25)


def _difference(left: object, right: object) -> float:
    try:
        result = float(left) - float(right)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _row_at_least(
    row: pd.Series | None,
    column: str,
    threshold: float,
) -> bool:
    if row is None:
        return False
    value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
    return bool(pd.notna(value) and float(value) >= threshold)


def _stage_confirmation_monotonic(target: pd.DataFrame) -> bool:
    rates = []
    for state in (
        "seller_exhaustion_watch",
        "early_bullish_reversal_watch",
        "structure_confirmed",
    ):
        rows = target.loc[
            target["state_slice"].eq(state)
            & target["slice_dimension"].eq("all")
        ]
        if len(rows) != 1:
            return False
        value = pd.to_numeric(
            pd.Series([rows.iloc[0]["confirmation_rate"]]),
            errors="coerce",
        ).iloc[0]
        if pd.isna(value):
            return False
        rates.append(float(value))
    return rates == sorted(rates)


def _ablations_beaten(
    metrics: pd.DataFrame,
    overall_row: pd.Series | None,
) -> int:
    if overall_row is None:
        return 0
    full_gain = pd.to_numeric(
        pd.Series([overall_row.get("return_gain")]),
        errors="coerce",
    ).iloc[0]
    if pd.isna(full_gain):
        return 0
    rows = metrics.loc[
        metrics["cohort"].eq("confirmation")
        & metrics["variant"].ne("full")
        & metrics["horizon"].eq(10)
        & metrics["scope"].eq("non_overlapping")
        & metrics["state_slice"].eq("early_states")
        & metrics["slice_dimension"].eq("all")
        & metrics["metric_scope"].eq("matched")
    ]
    gains = pd.to_numeric(rows["return_gain"], errors="coerce")
    return int(gains.lt(float(full_gain)).sum())


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(
        columns=list(PAIR_COLUMNS)
        + [
            f"{side}_{column}"
            for side in ("event", "baseline")
            for column in OUTCOME_COLUMNS
        ]
    )


def _baseline_index(
    baselines: pd.DataFrame,
    *,
    identity_column: str,
) -> dict[tuple[object, ...], tuple[tuple[pd.Timestamp, ...], dict]]:
    grouped: dict[tuple[object, ...], dict[pd.Timestamp, list[str]]] = {}
    ordered = baselines.sort_values(
        ["observation_date", "ticker", "event_id"],
        kind="stable",
    )
    for _, row in ordered.iterrows():
        key = (
            tuple(row[column] for column in MATCH_KEY_COLUMNS),
            row[identity_column],
            str(row["drawdown_bin"]),
        )
        date = pd.Timestamp(row["observation_date"])
        grouped.setdefault(key, {}).setdefault(date, []).append(
            str(row["event_id"])
        )
    return {
        key: (tuple(sorted(by_date)), by_date)
        for key, by_date in grouped.items()
    }


def _nearest_available_baseline(
    index: dict[tuple[object, ...], tuple[tuple[pd.Timestamp, ...], dict]],
    keys: tuple[tuple[object, ...], ...],
    event_date: pd.Timestamp,
    used: set[str],
    rows: dict[str, pd.Series],
) -> str | None:
    candidates = []
    for key in keys:
        indexed = index.get(key)
        if indexed is None:
            continue
        candidate = _nearest_in_index(
            indexed,
            event_date,
            used,
            rows,
        )
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda event_id: _baseline_rank(
            rows[event_id],
            event_date,
        ),
    )


def _nearest_in_index(
    indexed: tuple[tuple[pd.Timestamp, ...], dict],
    event_date: pd.Timestamp,
    used: set[str],
    rows: dict[str, pd.Series],
) -> str | None:
    dates, by_date = indexed
    right = bisect_left(dates, event_date)
    left = right - 1
    while left >= 0 or right < len(dates):
        candidate_dates = []
        if left >= 0:
            candidate_dates.append(dates[left])
        if right < len(dates):
            candidate_dates.append(dates[right])
        distance = min(abs(date - event_date) for date in candidate_dates)
        equally_near = [
            date
            for date in candidate_dates
            if abs(date - event_date) == distance
        ]
        available = [
            event_id
            for date in equally_near
            for event_id in by_date[date]
            if event_id not in used
        ]
        if available:
            return min(
                available,
                key=lambda event_id: _baseline_rank(
                    rows[event_id],
                    event_date,
                ),
            )
        for date in equally_near:
            if left >= 0 and dates[left] == date:
                left -= 1
            if right < len(dates) and dates[right] == date:
                right += 1
    return None


def _baseline_rank(
    row: pd.Series,
    event_date: pd.Timestamp,
) -> tuple[object, ...]:
    return (
        abs(pd.Timestamp(row["observation_date"]) - event_date),
        str(row["ticker"]),
        pd.Timestamp(row["observation_date"]),
        str(row["event_id"]),
    )


def _adjacent_drawdown_bins(drawdown_bin: str) -> tuple[str, ...]:
    try:
        position = DRAWNDOWN_BIN_ORDER.index(drawdown_bin)
    except ValueError:
        return ()
    return tuple(
        DRAWNDOWN_BIN_ORDER[index]
        for index in (position - 1, position + 1)
        if 0 <= index < len(DRAWNDOWN_BIN_ORDER)
    )


def _pair_row(
    event: pd.Series,
    baseline: pd.Series,
    *,
    match_tier: str,
) -> dict[str, object]:
    pair_id = f"{event['event_id']}::{baseline['event_id']}"
    row = {
        "pair_id": pair_id,
        "event_id": event["event_id"],
        "baseline_event_id": baseline["event_id"],
        "ticker": event["ticker"],
        "baseline_ticker": baseline["ticker"],
        "observation_date": event["observation_date"],
        "baseline_observation_date": baseline["observation_date"],
        "observation_state": event["observation_state"],
        "cohort": event["cohort"],
        "fold": event["fold"],
        "group": event["group"],
        "market_regime": event["market_regime"],
        "horizon": event["horizon"],
        "scope": event["scope"],
        "variant": event["variant"],
        "drawdown_bin": event["drawdown_bin"],
        "baseline_drawdown_bin": baseline["drawdown_bin"],
        "match_tier": match_tier,
        "matched": True,
    }
    for column in OUTCOME_COLUMNS:
        row[f"event_{column}"] = event.get(column)
        row[f"baseline_{column}"] = baseline.get(column)
    return row


def _validate_matching_events(events: pd.DataFrame) -> None:
    if not isinstance(events, pd.DataFrame):
        raise TypeError("events must be a DataFrame")
    missing = [
        column for column in MATCH_REQUIRED_COLUMNS if column not in events
    ]
    if missing:
        raise ValueError(f"events are missing matching columns: {missing}")
    if events["event_id"].astype(str).duplicated().any():
        raise ValueError("event_id must be unique")


def _candidate_positions(states: pd.DataFrame) -> list[int]:
    positions = []
    for position, row in enumerate(states.itertuples(index=False)):
        state = str(row.bottom_state)
        transition = bool(row.bottom_state_transition)
        if state == "downtrend_continuation":
            positions.append(position)
        elif transition and (
            state in POSITIVE_STATES or state == TERMINAL_FAILURE
        ):
            positions.append(position)
    return positions


def _non_overlapping_positions(
    positions: list[int],
    states: pd.DataFrame,
    *,
    non_overlap_sessions: int,
) -> list[int]:
    selected = []
    active_until = -1
    for position in positions:
        state = str(states.iloc[position]["bottom_state"])
        if state == "downtrend_continuation":
            selected.append(position)
            continue
        if state == TERMINAL_FAILURE:
            selected.append(position)
            active_until = -1
            continue
        if position < active_until:
            continue
        selected.append(position)
        active_until = position + non_overlap_sessions
    return selected


def _event_row(
    ticker: str,
    history: pd.DataFrame,
    states: pd.DataFrame,
    drawdown: pd.Series,
    *,
    position: int,
    horizon: int,
    scope: str,
) -> dict[str, object]:
    observation = states.iloc[position]
    observation_state = str(observation["bottom_state"])
    observation_close = float(history["Close"].iloc[position])
    future = history.iloc[position + 1 : position + horizon + 1]
    terminal = states.iloc[position + 1 : position + horizon + 1]
    confirmation_delay = (
        0 if observation_state in STRUCTURE_STATES else None
    )
    failure_delay = 0 if observation_state == TERMINAL_FAILURE else None
    if confirmation_delay is None or failure_delay is None:
        for delay, (_, state_row) in enumerate(
            terminal.iterrows(),
            start=1,
        ):
            state = str(state_row["bottom_state"])
            raw_state = str(state_row.get("bottom_raw_state") or state)
            if failure_delay is None and state == TERMINAL_FAILURE:
                failure_delay = delay
            if (
                confirmation_delay is None
                and (state in STRUCTURE_STATES or raw_state in STRUCTURE_STATES)
            ):
                confirmation_delay = delay
    first_terminal = _first_terminal_state(
        confirmation_delay,
        failure_delay,
    )
    terminal_state = str(terminal.iloc[-1]["bottom_state"])
    maintained = bool(
        observation_state in POSITIVE_STATES
        and failure_delay is None
        and terminal_state in POSITIVE_STATES
        and STATE_RANK[terminal_state] >= STATE_RANK[observation_state]
    )
    observation_drawdown = float(drawdown.iloc[position])
    return {
        "event_id": (
            f"{ticker}:{history.index[position].date().isoformat()}:"
            f"{observation_state}:{scope}:{horizon}"
        ),
        "ticker": ticker,
        "observation_date": history.index[position],
        "event_end_date": history.index[position + horizon],
        "observation_state": observation_state,
        "observation_rank": STATE_RANK[observation_state],
        "event_role": (
            "baseline"
            if observation_state == "downtrend_continuation"
            else "event"
        ),
        "scope": scope,
        "horizon": horizon,
        "observation_close": observation_close,
        "drawdown_63": observation_drawdown,
        "drawdown_bin": _drawdown_bin(observation_drawdown),
        "forward_return": (
            float(future["Close"].iloc[-1]) / observation_close - 1.0
        ),
        "positive_return": bool(
            float(future["Close"].iloc[-1]) > observation_close
        ),
        "maximum_favorable_excursion": (
            float(future["High"].max()) / observation_close - 1.0
        ),
        "maximum_adverse_excursion": (
            float(future["Low"].min()) / observation_close - 1.0
        ),
        "confirmed_within_horizon": confirmation_delay is not None,
        "failed_within_horizon": failure_delay is not None,
        "first_terminal_state": first_terminal,
        "sessions_to_confirmation": confirmation_delay,
        "sessions_to_failure": failure_delay,
        "state_maintained": maintained,
        "bottom_score": _optional_float(observation["bottom_score"]),
        "bottom_coverage": _optional_float(
            observation["bottom_coverage"]
        ),
        "bottom_state_age_sessions": _optional_integer(
            observation["bottom_state_age_sessions"]
        ),
    }


def _first_terminal_state(
    confirmation_delay: int | None,
    failure_delay: int | None,
) -> str | None:
    if failure_delay is not None and (
        confirmation_delay is None or failure_delay <= confirmation_delay
    ):
        return "failed"
    if confirmation_delay is not None:
        return "confirmed"
    return None


def _drawdown_bin(value: float) -> str:
    if not math.isfinite(value):
        return "unavailable"
    if value >= -0.15:
        return "0_-15"
    if value >= -0.25:
        return "-15_-25"
    if value >= -0.40:
        return "-25_-40"
    return "below_-40"


def _validate_inputs(
    history: pd.DataFrame,
    states: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    non_overlap_sessions: int,
) -> tuple[int, ...]:
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    if not isinstance(states, pd.DataFrame):
        raise TypeError("states must be a DataFrame")
    missing_prices = [
        column for column in REQUIRED_PRICE_COLUMNS if column not in history
    ]
    if missing_prices:
        raise ValueError(f"history is missing required columns: {missing_prices}")
    missing_states = [
        column for column in REQUIRED_STATE_COLUMNS if column not in states
    ]
    if missing_states:
        raise ValueError(f"states are missing required columns: {missing_states}")
    if not history.index.equals(states.index):
        raise ValueError("history and states must align")
    if history.index.has_duplicates or not history.index.is_monotonic_increasing:
        raise ValueError("history dates must be unique and increasing")
    values = history.loc[:, REQUIRED_PRICE_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("history OHLCV values must be finite")
    if (values[:, :4] <= 0.0).any() or (values[:, 4] < 0.0).any():
        raise ValueError("history OHLC prices must be positive and volume nonnegative")
    if (
        not horizons
        or any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            for value in horizons
        )
    ):
        raise ValueError("horizons must contain unique positive integers")
    checked = tuple(int(value) for value in horizons)
    if len(checked) != len(set(checked)) or any(
        value <= 0 for value in checked
    ):
        raise ValueError("horizons must contain unique positive integers")
    if (
        isinstance(non_overlap_sessions, bool)
        or not isinstance(non_overlap_sessions, int)
        or non_overlap_sessions <= 0
    ):
        raise ValueError("non_overlap_sessions must be a positive integer")
    unknown = set(states["bottom_state"].dropna().astype(str)) - set(
        STATE_RANK
    )
    if unknown:
        raise ValueError(f"unknown bottom states: {sorted(unknown)}")
    return checked


def _optional_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_integer(value: object) -> int | None:
    number = _optional_float(value)
    return None if number is None else int(number)
