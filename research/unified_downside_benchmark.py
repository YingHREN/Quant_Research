"""Leakage-safe primitives for the unified downside walk-forward benchmark."""

from __future__ import annotations

import math
import json
from collections.abc import Mapping

import numpy as np
import pandas as pd


DEFAULT_ADVERSE_THRESHOLDS = {
    5: -0.05,
    10: -0.075,
    20: -0.10,
}
PRICE_COLUMNS = ("Open", "High", "Low", "Close")
MODEL_KEY_COLUMNS = ("ticker", "observation_date", "horizon", "fold")
MODEL_OUTPUT_COLUMNS = (
    "specification",
    "predicted_event",
    "predicted_score",
    "available_at_close",
    "executable_at",
    "model_version",
    "status",
)


def attach_next_open_path_targets(
    frame: pd.DataFrame,
    horizons=(5, 10, 20),
    adverse_thresholds: Mapping[int, float] | None = None,
) -> pd.DataFrame:
    """Attach executable future-path outcomes without dropping the tail."""
    checked = _validate_price_frame(frame)
    checked_horizons = _validate_horizons(horizons)
    thresholds = _validate_thresholds(
        checked_horizons,
        DEFAULT_ADVERSE_THRESHOLDS
        if adverse_thresholds is None
        else adverse_thresholds,
    )
    parts = []
    for ticker, source in checked.groupby(level="ticker", sort=True):
        history = source.droplevel("ticker").sort_index()
        positions = pd.Series(
            np.arange(len(history), dtype=int),
            index=history.index,
        )
        numeric = history.loc[:, PRICE_COLUMNS].apply(
            pd.to_numeric,
            errors="coerce",
        )
        finite_row = pd.Series(
            np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1),
            index=history.index,
        )
        for horizon in checked_horizons:
            entry_open = numeric["Open"].shift(-1)
            terminal_close = numeric["Close"].shift(-horizon)
            future_low = _forward_window(
                numeric["Low"].shift(-1),
                horizon,
                "min",
            )
            future_high = _forward_window(
                numeric["High"].shift(-1),
                horizon,
                "max",
            )
            future_finite = _forward_window(
                finite_row.astype(float).shift(-1),
                horizon,
                "min",
            ).eq(1.0)
            has_window = positions + horizon < len(history)
            mature = (
                has_window
                & future_finite
                & entry_open.notna()
                & np.isfinite(entry_open)
                & entry_open.gt(0.0)
                & terminal_close.notna()
                & np.isfinite(terminal_close)
            )
            result = pd.DataFrame(index=history.index)
            result["ticker"] = ticker
            result["horizon"] = horizon
            result["entry_open"] = entry_open.where(mature)
            result["terminal_return"] = (
                terminal_close / entry_open - 1.0
            ).where(mature)
            result["mae"] = (future_low / entry_open - 1.0).where(mature)
            result["mfe"] = (future_high / entry_open - 1.0).where(mature)
            result["mature"] = mature.astype(bool)
            result["immature"] = (~mature).astype(bool)
            result["unavailable_reason"] = pd.Series(
                np.where(
                    mature,
                    None,
                    np.where(
                        has_window,
                        "invalid_future_path",
                        "immature_future_path",
                    ),
                ),
                index=history.index,
                dtype="object",
            )
            actual = pd.Series(
                pd.NA,
                index=history.index,
                dtype="boolean",
            )
            actual.loc[mature] = (
                result.loc[mature, "mae"] <= thresholds[horizon]
            )
            result["actual_event"] = actual
            result.index.name = "observation_date"
            parts.append(
                result.reset_index().set_index(
                    ["ticker", "observation_date", "horizon"]
                )
            )
    if not parts:
        return _empty_target_frame()
    output = pd.concat(parts).sort_index()
    output.index = output.index.set_names(
        ["ticker", "observation_date", "horizon"]
    )
    return output


def align_model_predictions(
    labels: pd.DataFrame,
    predictions: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Align every model to the frozen label keys without filling absences."""
    if not isinstance(predictions, Mapping):
        raise TypeError("predictions must be a mapping")
    base = _normalize_model_keys(labels, "labels")
    if base.duplicated(list(MODEL_KEY_COLUMNS)).any():
        raise ValueError("labels contain duplicate test keys")
    base_keys = base.loc[:, MODEL_KEY_COLUMNS]
    output = []
    for raw_specification, source in predictions.items():
        specification = str(raw_specification).strip()
        if not specification:
            raise ValueError("prediction specification must not be blank")
        model = _normalize_model_keys(source, specification)
        if model.duplicated(list(MODEL_KEY_COLUMNS)).any():
            raise ValueError(
                f"{specification} predictions contain duplicate test keys"
            )
        required = {"predicted_event", "model_version"}
        missing = sorted(required.difference(model.columns))
        if missing:
            raise ValueError(
                f"{specification} is missing prediction columns: {missing}"
            )
        outside = model.loc[:, MODEL_KEY_COLUMNS].merge(
            base_keys,
            on=list(MODEL_KEY_COLUMNS),
            how="left",
            indicator=True,
            validate="one_to_one",
        )
        if (outside["_merge"] == "left_only").any():
            raise ValueError(
                f"{specification} contains rows outside frozen test keys"
            )
        selected = model.loc[
            :,
            [
                *MODEL_KEY_COLUMNS,
                *(
                    column
                    for column in (
                        "predicted_event",
                        "predicted_score",
                        "available_at_close",
                        "executable_at",
                        "model_version",
                    )
                    if column in model
                ),
            ],
        ].copy()
        merged = base.merge(
            selected,
            on=list(MODEL_KEY_COLUMNS),
            how="left",
            validate="one_to_one",
        )
        merged["specification"] = specification
        event = merged["predicted_event"].astype("boolean")
        merged["predicted_event"] = event
        merged["predicted_score"] = pd.to_numeric(
            merged.get("predicted_score"),
            errors="coerce",
        )
        provided = event.notna()
        if "available_at_close" not in merged:
            merged["available_at_close"] = provided
        else:
            merged["available_at_close"] = (
                merged["available_at_close"].astype("boolean").where(
                    provided,
                    False,
                )
            )
        if "executable_at" not in merged:
            merged["executable_at"] = "next_open"
        else:
            merged["executable_at"] = merged["executable_at"].where(
                provided,
                "unavailable",
            )
        merged["status"] = np.where(
            provided,
            "available",
            "unavailable",
        )
        merged["model_version"] = merged["model_version"].where(
            provided,
            None,
        )
        output.append(merged)
    if not output:
        return pd.DataFrame(
            columns=[*base.columns, *MODEL_OUTPUT_COLUMNS]
        )
    return pd.concat(output, ignore_index=True, sort=False).sort_values(
        [*MODEL_KEY_COLUMNS, "specification"],
        kind="stable",
    ).reset_index(drop=True)


def attach_point_in_time_strata(
    frame: pd.DataFrame,
    assignments: pd.DataFrame,
    regimes: pd.DataFrame,
) -> pd.DataFrame:
    """Attach half-open historical groups and same-date market regimes."""
    checked = _normalize_observation_rows(frame)
    assignment_rows = _normalize_assignments(assignments)
    checked["group_key"] = "unclassified"
    checked["group_source"] = None
    checked["classification_state"] = "unclassified"
    for ticker, positions in checked.groupby("ticker", sort=False).groups.items():
        history = assignment_rows.loc[
            assignment_rows["ticker"] == ticker
        ].sort_values("effective_from")
        if history.empty:
            continue
        starts = history["effective_from"].to_numpy(dtype="datetime64[ns]")
        ends = history["effective_to"].to_numpy(dtype="datetime64[ns]")
        dates = checked.loc[
            positions,
            "observation_date",
        ].to_numpy(dtype="datetime64[ns]")
        selected = np.searchsorted(starts, dates, side="right") - 1
        valid_position = selected >= 0
        safe_selected = np.maximum(selected, 0)
        selected_ends = ends[safe_selected]
        valid = valid_position & (
            np.isnat(selected_ends) | (dates < selected_ends)
        )
        for row_position, assignment_position, is_valid in zip(
            positions,
            safe_selected,
            valid,
        ):
            if not is_valid:
                continue
            assignment = history.iloc[int(assignment_position)]
            checked.at[row_position, "group_key"] = _study_group(assignment)
            checked.at[row_position, "group_source"] = assignment["source"]
            checked.at[
                row_position,
                "classification_state",
            ] = assignment["classification_state"]
    regime_rows = _normalize_regimes(regimes)
    checked = checked.merge(
        regime_rows,
        on="observation_date",
        how="left",
        validate="many_to_one",
    )
    checked["market_regime"] = checked["market_regime"].fillna(
        "unavailable"
    )
    return checked


def _forward_window(series, window, operation):
    reversed_series = series.iloc[::-1]
    rolling = reversed_series.rolling(window, min_periods=window)
    if operation == "min":
        values = rolling.min()
    elif operation == "max":
        values = rolling.max()
    else:  # pragma: no cover - private caller freezes the operations.
        raise ValueError(f"unsupported operation: {operation}")
    return values.iloc[::-1]


def _normalize_model_keys(frame, label):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{label} must be a DataFrame")
    checked = frame.copy(deep=True)
    if isinstance(checked.index, pd.MultiIndex):
        names = set(checked.index.names)
        if set(MODEL_KEY_COLUMNS).issubset(names):
            checked = checked.reset_index()
        elif {"ticker", "observation_date", "horizon"}.issubset(names):
            checked = checked.reset_index()
    missing = sorted(set(MODEL_KEY_COLUMNS).difference(checked.columns))
    if missing:
        raise ValueError(f"{label} is missing model keys: {missing}")
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    checked["horizon"] = pd.to_numeric(
        checked["horizon"],
        errors="raise",
    ).astype(int)
    checked["fold"] = pd.to_numeric(
        checked["fold"],
        errors="raise",
    ).astype(int)
    return checked


def _normalize_observation_rows(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    checked = frame.copy(deep=True)
    if isinstance(checked.index, pd.MultiIndex):
        if {"ticker", "observation_date"}.issubset(checked.index.names):
            checked = checked.reset_index()
    missing = sorted(
        {"ticker", "observation_date"}.difference(checked.columns)
    )
    if missing:
        raise ValueError(f"frame is missing observation keys: {missing}")
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    return checked.reset_index(drop=True)


def _normalize_assignments(assignments):
    columns = (
        "ticker",
        "effective_from",
        "effective_to",
        "primary_model_group",
        "classification_state",
        "source",
        "theme_keys",
    )
    if assignments is None or (
        isinstance(assignments, pd.DataFrame) and assignments.empty
    ):
        return pd.DataFrame(columns=columns)
    if not isinstance(assignments, pd.DataFrame):
        raise TypeError("assignments must be a DataFrame")
    checked = assignments.copy(deep=True)
    required = {
        "ticker",
        "effective_from",
        "effective_to",
        "primary_model_group",
        "classification_state",
        "source",
    }
    missing = sorted(required.difference(checked.columns))
    if missing:
        raise ValueError(f"assignments are missing columns: {missing}")
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["effective_from"] = pd.to_datetime(
        checked["effective_from"],
        errors="raise",
    ).dt.tz_localize(None)
    checked["effective_to"] = pd.to_datetime(
        checked["effective_to"],
        errors="coerce",
    ).dt.tz_localize(None)
    if "theme_keys" in checked:
        checked["theme_keys"] = checked["theme_keys"].map(
            _parse_theme_keys
        )
    elif "theme_keys_json" in checked:
        checked["theme_keys"] = checked["theme_keys_json"].map(
            _parse_theme_keys
        )
    else:
        checked["theme_keys"] = [tuple()] * len(checked)
    checked = checked.sort_values(["ticker", "effective_from"])
    for ticker, rows in checked.groupby("ticker", sort=False):
        prior_end = None
        for row in rows.itertuples():
            if (
                row.effective_to is not pd.NaT
                and pd.notna(row.effective_to)
                and row.effective_to <= row.effective_from
            ):
                raise ValueError(f"invalid assignment interval for {ticker}")
            if prior_end is None and rows.index[0] != row.Index:
                raise ValueError(f"overlapping assignments for {ticker}")
            if prior_end is not None and row.effective_from < prior_end:
                raise ValueError(f"overlapping assignments for {ticker}")
            prior_end = (
                None if pd.isna(row.effective_to) else row.effective_to
            )
    return checked.reset_index(drop=True)


def _parse_theme_keys(value):
    if value is None or value is pd.NA:
        return tuple()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return tuple()
        if stripped.startswith("["):
            value = json.loads(stripped)
        else:
            value = (stripped,)
    if isinstance(value, (list, tuple, set)):
        return tuple(
            str(item).strip()
            for item in value
            if str(item).strip()
        )
    raise ValueError("invalid theme keys")


def _study_group(assignment):
    if str(assignment["classification_state"]) != "classified":
        return "unclassified"
    keys = {
        str(assignment["primary_model_group"]).strip(),
        *(str(key).strip() for key in assignment["theme_keys"]),
    }
    if "semiconductor" in keys:
        return "semiconductor"
    if keys.intersection({"software", "software_cloud"}):
        return "software_cloud"
    return "other"


def _normalize_regimes(regimes):
    if regimes is None or (
        isinstance(regimes, pd.DataFrame) and regimes.empty
    ):
        return pd.DataFrame(
            columns=["observation_date", "market_regime"]
        )
    if not isinstance(regimes, pd.DataFrame):
        raise TypeError("regimes must be a DataFrame")
    checked = regimes.copy(deep=True)
    if "observation_date" not in checked:
        if isinstance(checked.index, pd.DatetimeIndex):
            checked = checked.reset_index().rename(
                columns={checked.index.name or "index": "observation_date"}
            )
        else:
            raise ValueError("regimes require observation_date")
    source_column = (
        "market_regime"
        if "market_regime" in checked
        else "regime"
        if "regime" in checked
        else None
    )
    if source_column is None:
        raise ValueError("regimes require regime state")
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    if checked["observation_date"].duplicated().any():
        raise ValueError("regimes contain duplicate observation dates")
    return checked.loc[
        :,
        ["observation_date", source_column],
    ].rename(columns={source_column: "market_regime"})


def _validate_price_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    checked = frame.copy(deep=True)
    if not isinstance(checked.index, pd.MultiIndex):
        required = {"ticker", "observation_date"}
        if not required.issubset(checked.columns):
            raise ValueError("frame requires ticker and observation_date")
        checked = checked.set_index(["ticker", "observation_date"])
    checked.index = checked.index.set_names(["ticker", "observation_date"])
    if checked.index.has_duplicates:
        raise ValueError("frame contains duplicate point-in-time keys")
    missing = sorted(set(PRICE_COLUMNS).difference(checked.columns))
    if missing:
        raise ValueError(f"frame is missing price columns: {missing}")
    ticker = (
        checked.index.get_level_values("ticker")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    dates = pd.to_datetime(
        checked.index.get_level_values("observation_date"),
        errors="raise",
    ).tz_localize(None)
    checked.index = pd.MultiIndex.from_arrays(
        [ticker, dates],
        names=["ticker", "observation_date"],
    )
    if checked.index.has_duplicates:
        raise ValueError("frame contains duplicate normalized keys")
    return checked.sort_index()


def _validate_horizons(horizons):
    try:
        values = tuple(horizons)
    except TypeError as exc:
        raise TypeError("horizons must be iterable") from exc
    if not values:
        raise ValueError("horizons must not be empty")
    checked = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError("horizons must contain positive integers")
        horizon = int(value)
        if horizon <= 0:
            raise ValueError("horizons must contain positive integers")
        checked.append(horizon)
    if len(set(checked)) != len(checked):
        raise ValueError("horizons must be unique")
    return tuple(checked)


def _validate_thresholds(horizons, thresholds):
    if not isinstance(thresholds, Mapping):
        raise TypeError("adverse_thresholds must be a mapping")
    missing = [horizon for horizon in horizons if horizon not in thresholds]
    if missing:
        raise ValueError(f"missing adverse threshold for horizons: {missing}")
    checked = {}
    for horizon in horizons:
        value = thresholds[horizon]
        if isinstance(value, bool):
            raise ValueError("adverse threshold must be finite and negative")
        threshold = float(value)
        if not math.isfinite(threshold) or threshold >= 0.0:
            raise ValueError("adverse threshold must be finite and negative")
        checked[horizon] = threshold
    return checked


def _empty_target_frame():
    index = pd.MultiIndex.from_arrays(
        [[], [], []],
        names=["ticker", "observation_date", "horizon"],
    )
    return pd.DataFrame(
        {
            "entry_open": pd.Series(dtype=float),
            "terminal_return": pd.Series(dtype=float),
            "mae": pd.Series(dtype=float),
            "mfe": pd.Series(dtype=float),
            "mature": pd.Series(dtype=bool),
            "immature": pd.Series(dtype=bool),
            "unavailable_reason": pd.Series(dtype=object),
            "actual_event": pd.Series(dtype="boolean"),
        },
        index=index,
    )
