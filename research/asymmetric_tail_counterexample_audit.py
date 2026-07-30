"""Point-in-time descriptive audit for asymmetric-tail counterexamples."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


SOURCE_DOWN_PROBABILITY = 0.40
SOURCE_TERMINAL_RETURN = 0.10
INDEX_NAMES = ("ticker", "observation_date")
SUMMARY_DIMENSIONS = (
    ("group", "group"),
    ("regime", "regime"),
    ("opening_gap", "opening_gap_band"),
    ("realized_volatility", "realized_volatility_band"),
    ("atr20", "atr20_band"),
    ("price", "price_band"),
    ("dollar_volume", "dollar_volume_band"),
    ("earnings_proximity", "earnings_proximity_status"),
)


def attach_point_in_time_context(
    counterexamples: pd.DataFrame,
    feature_frame: pd.DataFrame,
    *,
    point_in_time_groups=None,
) -> pd.DataFrame:
    """Attach exact observation-date context and fixed descriptive bands."""
    checked = _validate_counterexamples(counterexamples)
    features = _validate_feature_frame(feature_frame)
    keys = pd.MultiIndex.from_arrays(
        (
            checked["ticker"],
            checked["observation_date"],
        ),
        names=INDEX_NAMES,
    )
    missing_keys = keys[~keys.isin(features.index)]
    if len(missing_keys):
        preview = [
            (str(ticker), pd.Timestamp(date).date().isoformat())
            for ticker, date in missing_keys[:5]
        ]
        raise ValueError(
            "counterexample feature keys are unavailable: "
            f"{preview}"
        )

    context = features.loc[
        :,
        ["close", "atr20_pct", "realized_vol_63"],
    ].copy()
    context["atr20_percentile"] = context["atr20_pct"].groupby(
        level="observation_date",
        sort=False,
    ).rank(method="average", pct=True)
    context["realized_volatility_percentile"] = context[
        "realized_vol_63"
    ].groupby(
        level="observation_date",
        sort=False,
    ).rank(method="average", pct=True)
    selected = context.reindex(keys)

    result = checked.copy(deep=True)
    result["price"] = selected["close"].to_numpy(dtype=float)
    result["atr20_pct"] = selected["atr20_pct"].to_numpy(dtype=float)
    result["realized_volatility"] = selected[
        "realized_vol_63"
    ].to_numpy(dtype=float)
    result["atr20_percentile"] = selected[
        "atr20_percentile"
    ].to_numpy(dtype=float)
    result["realized_volatility_percentile"] = selected[
        "realized_volatility_percentile"
    ].to_numpy(dtype=float)
    result["opening_gap_band"] = _opening_gap_band(
        result["opening_gap"]
    )
    result["realized_volatility_band"] = _percentile_band(
        result["realized_volatility_percentile"]
    )
    result["atr20_band"] = _percentile_band(
        result["atr20_percentile"]
    )
    result["price_band"] = _fixed_band(
        result["price"],
        boundaries=(5.0, 20.0, 100.0),
        labels=("below_5", "5_to_20", "20_to_100", "at_least_100"),
        right=False,
    )
    result["dollar_volume_band"] = _fixed_band(
        result["dollar_volume"],
        boundaries=(10_000_000.0, 100_000_000.0, 1_000_000_000.0),
        labels=(
            "below_10m",
            "10m_to_100m",
            "100m_to_1b",
            "at_least_1b",
        ),
        right=False,
    )
    result["earnings_proximity_status"] = "unavailable"
    result["published_group"] = _string_or_unavailable(result["group"])
    normalized_groups = _normalize_point_in_time_groups(
        point_in_time_groups
    )
    result["group"] = [
        normalized_groups.get((ticker, date), "unavailable")
        for ticker, date in zip(
            result["ticker"],
            result["observation_date"],
        )
    ]
    result["point_in_time_group_status"] = np.where(
        result["group"].eq("unavailable"),
        "unavailable",
        "available",
    )
    result["regime"] = _string_or_unavailable(result["regime"])
    return result


def resolve_point_in_time_groups(
    counterexamples: pd.DataFrame,
    intervals: pd.DataFrame,
) -> dict:
    """Resolve only assignments observable by each counterexample date."""
    if not isinstance(counterexamples, pd.DataFrame):
        raise TypeError("counterexamples must be a DataFrame")
    if not isinstance(intervals, pd.DataFrame):
        raise TypeError("intervals must be a DataFrame")
    required_keys = {"ticker", "observation_date"}
    required_intervals = {
        "ticker",
        "effective_from",
        "effective_to",
        "group",
        "source",
        "observed_at",
    }
    if not required_keys.issubset(counterexamples.columns):
        raise ValueError("counterexamples are missing group lookup keys")
    missing = sorted(required_intervals.difference(intervals.columns))
    if missing:
        raise ValueError(f"group intervals are missing columns: {missing}")
    keys = counterexamples.loc[:, ["ticker", "observation_date"]].copy()
    keys["ticker"] = keys["ticker"].astype(str).str.strip().str.upper()
    keys["observation_date"] = _naive_datetimes(keys["observation_date"])
    keys["_order"] = np.arange(len(keys))

    evidence = intervals.loc[:, sorted(required_intervals)].copy()
    evidence["ticker"] = (
        evidence["ticker"].astype(str).str.strip().str.upper()
    )
    evidence["effective_from"] = _naive_datetimes(
        evidence["effective_from"]
    )
    evidence["effective_to"] = pd.to_datetime(
        evidence["effective_to"],
        errors="coerce",
    )
    if evidence["effective_to"].dt.tz is not None:
        evidence["effective_to"] = evidence["effective_to"].dt.tz_convert(
            None
        )
    evidence["observed_at"] = _naive_datetimes(evidence["observed_at"])
    evidence["group"] = _string_or_unavailable(evidence["group"])
    sources = evidence["source"].fillna("").astype(str).str.casefold()
    evidence = evidence.loc[
        sources.ne("")
        & ~sources.str.contains("historical_backfill_assumption")
    ]
    if evidence.empty:
        return {}
    joined = keys.merge(evidence, on="ticker", how="left")
    eligible = joined.loc[
        joined["group"].ne("unavailable")
        & (joined["observed_at"] <= joined["observation_date"])
        & (joined["effective_from"] <= joined["observation_date"])
        & (
            joined["effective_to"].isna()
            | (joined["observation_date"] < joined["effective_to"])
        )
    ].copy()
    if eligible.duplicated("_order").any():
        raise ValueError("ambiguous point-in-time group assignment")
    return {
        (
            str(row.ticker),
            pd.Timestamp(row.observation_date),
        ): str(row.group)
        for row in eligible.itertuples(index=False)
    }


def summarize_counterexamples(audit_rows: pd.DataFrame) -> pd.DataFrame:
    """Summarize raw outcomes without trimming or winsorization."""
    if not isinstance(audit_rows, pd.DataFrame):
        raise TypeError("audit_rows must be a DataFrame")
    required = (
        "actual_terminal_return",
        "actual_path_mae",
        "calibrated_down_probability",
        "calibrated_rebound_probability",
        *(column for _, column in SUMMARY_DIMENSIONS),
    )
    missing = [column for column in required if column not in audit_rows]
    if missing:
        raise ValueError(f"audit_rows are missing columns: {missing}")
    if audit_rows.empty:
        return pd.DataFrame(columns=_summary_columns())
    rows = [
        _summary_row(
            audit_rows,
            dimension="overall",
            stratum="all",
            total_count=len(audit_rows),
        )
    ]
    for dimension, column in SUMMARY_DIMENSIONS:
        for stratum, selected in audit_rows.groupby(
            column,
            sort=True,
            dropna=False,
        ):
            rows.append(
                _summary_row(
                    selected,
                    dimension=dimension,
                    stratum=(
                        "unavailable"
                        if pd.isna(stratum)
                        else str(stratum)
                    ),
                    total_count=len(audit_rows),
                )
            )
    return pd.DataFrame(rows, columns=_summary_columns())


def preregistered_feature_hypotheses() -> tuple[dict, ...]:
    """Return fixed next-study hypotheses without granting model authority."""
    common = {
        "status": "preregistered",
        "lifecycle": "research",
        "online_authority": "none",
    }
    return (
        {
            **common,
            "name": "gap_discontinuity_interaction",
            "fields": (
                "opening_gap",
                "calibrated_down_probability",
                "calibrated_rebound_probability",
            ),
            "test": (
                "只在训练折拟合跳空交互，并要求样本外极端反弹误报下降"
            ),
        },
        {
            **common,
            "name": "cross_sectional_volatility_interaction",
            "fields": (
                "realized_volatility_percentile",
                "atr20_percentile",
            ),
            "test": (
                "在嵌套折比较固定百分位档，不修改已发布尾部阈值"
            ),
        },
        {
            **common,
            "name": "price_liquidity_nonlinearity",
            "fields": ("price", "dollar_volume"),
            "test": (
                "在每个外层折比较固定价格/成交额交互与现有特征集"
            ),
        },
        {
            **common,
            "name": "earnings_calendar_availability",
            "fields": ("earnings_proximity",),
            "test": (
                "在版本化点时财报日历可用前保持缺失，接入前另行冻结日期窗口"
            ),
        },
    )


def fixed_band_definitions() -> Mapping:
    """Expose the descriptive bands used by the audit report."""
    return {
        "opening_gap": (
            "gap_down_3pct_or_more: x < -0.03",
            "within_3pct: -0.03 <= x < 0.03",
            "gap_up_3pct_or_more: x >= 0.03",
        ),
        "percentile": (
            "low_25pct: x <= 0.25",
            "middle_50pct: 0.25 < x <= 0.75",
            "high_25pct: x > 0.75",
        ),
        "price": (
            "below_5: x < 5",
            "5_to_20: 5 <= x < 20",
            "20_to_100: 20 <= x < 100",
            "at_least_100: x >= 100",
        ),
        "dollar_volume": (
            "below_10m: x < 10000000",
            "10m_to_100m: 10000000 <= x < 100000000",
            "100m_to_1b: 100000000 <= x < 1000000000",
            "at_least_1b: x >= 1000000000",
        ),
    }


def _validate_counterexamples(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("counterexamples must be a DataFrame")
    required = (
        "ticker",
        "observation_date",
        "group",
        "regime",
        "calibrated_down_probability",
        "calibrated_rebound_probability",
        "actual_terminal_return",
        "actual_path_mae",
        "opening_gap",
        "dollar_volume",
    )
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"counterexamples are missing columns: {missing}")
    checked = frame.copy(deep=True)
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["observation_date"] = _naive_datetimes(
        checked["observation_date"]
    )
    if checked["ticker"].eq("").any():
        raise ValueError("counterexample tickers must not be empty")
    if checked.duplicated(list(INDEX_NAMES)).any():
        raise ValueError("counterexamples contain duplicate ticker/date keys")
    numeric = (
        "calibrated_down_probability",
        "calibrated_rebound_probability",
        "actual_terminal_return",
        "actual_path_mae",
        "opening_gap",
        "dollar_volume",
    )
    for column in numeric:
        checked[column] = pd.to_numeric(checked[column], errors="coerce")
    defining = checked[
        ["calibrated_down_probability", "actual_terminal_return"]
    ].to_numpy(dtype=float)
    if (
        not np.isfinite(defining).all()
        or (checked["calibrated_down_probability"] < SOURCE_DOWN_PROBABILITY)
        .any()
        or (checked["actual_terminal_return"] < SOURCE_TERMINAL_RETURN).any()
    ):
        raise ValueError(
            "counterexamples violate the frozen source sample definition"
        )
    return checked


def _validate_feature_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("feature_frame must be a DataFrame")
    if not isinstance(frame.index, pd.MultiIndex):
        raise ValueError("feature_frame must use a MultiIndex")
    if tuple(frame.index.names) != INDEX_NAMES:
        raise ValueError(f"feature_frame index names must be {INDEX_NAMES}")
    if not frame.index.is_unique:
        raise ValueError("feature_frame index must be unique")
    missing = [
        column
        for column in ("close", "atr20_pct", "realized_vol_63")
        if column not in frame
    ]
    if missing:
        raise ValueError(f"feature_frame is missing columns: {missing}")
    checked = frame.loc[
        :,
        ["close", "atr20_pct", "realized_vol_63"],
    ].copy()
    tickers = checked.index.get_level_values("ticker").astype(
        str
    ).str.strip().str.upper()
    dates = _naive_datetimes(
        checked.index.get_level_values("observation_date")
    )
    checked.index = pd.MultiIndex.from_arrays(
        (tickers, dates),
        names=INDEX_NAMES,
    )
    if not checked.index.is_unique:
        raise ValueError("normalized feature_frame index must be unique")
    for column in checked:
        checked[column] = pd.to_numeric(checked[column], errors="coerce")
    return checked.sort_index()


def _fixed_band(values, *, boundaries, labels, right=True):
    numeric = pd.to_numeric(values, errors="coerce")
    bins = (-np.inf, *boundaries, np.inf)
    banded = pd.cut(
        numeric,
        bins=bins,
        labels=labels,
        right=right,
        include_lowest=True,
    ).astype("object")
    return pd.Series(banded, index=values.index).fillna("unavailable")


def _opening_gap_band(values):
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series("unavailable", index=values.index, dtype="object")
    result.loc[numeric < -0.03] = "gap_down_3pct_or_more"
    result.loc[(numeric >= -0.03) & (numeric < 0.03)] = "within_3pct"
    result.loc[numeric >= 0.03] = "gap_up_3pct_or_more"
    return result


def _percentile_band(values):
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series("unavailable", index=values.index, dtype="object")
    result.loc[numeric <= 0.25] = "low_25pct"
    result.loc[(numeric > 0.25) & (numeric <= 0.75)] = "middle_50pct"
    result.loc[numeric > 0.75] = "high_25pct"
    return result


def _string_or_unavailable(values):
    normalized = values.fillna("").astype(str).str.strip()
    return normalized.mask(normalized.eq(""), "unavailable")


def _normalize_point_in_time_groups(groups):
    if groups is None:
        return {}
    if not isinstance(groups, Mapping):
        raise TypeError("point_in_time_groups must be a mapping or None")
    result = {}
    for raw_key, raw_group in groups.items():
        if (
            not isinstance(raw_key, tuple)
            or len(raw_key) != 2
        ):
            raise ValueError(
                "point_in_time_groups keys must be ticker/date tuples"
            )
        ticker = str(raw_key[0]).strip().upper()
        date = pd.Timestamp(raw_key[1])
        if date.tzinfo is not None:
            date = date.tz_convert(None)
        group = str(raw_group).strip()
        if not ticker or not group:
            raise ValueError("point_in_time_groups values must not be empty")
        result[(ticker, date.normalize())] = group
    return result


def _summary_row(frame, *, dimension, stratum, total_count):
    return {
        "dimension": str(dimension),
        "stratum": str(stratum),
        "row_count": len(frame),
        "share": len(frame) / total_count,
        "mean_terminal_return": float(
            frame["actual_terminal_return"].mean()
        ),
        "median_terminal_return": float(
            frame["actual_terminal_return"].median()
        ),
        "median_path_mae": float(frame["actual_path_mae"].median()),
        "median_down_probability": float(
            frame["calibrated_down_probability"].median()
        ),
        "median_rebound_probability": float(
            frame["calibrated_rebound_probability"].median()
        ),
    }


def _summary_columns():
    return (
        "dimension",
        "stratum",
        "row_count",
        "share",
        "mean_terminal_return",
        "median_terminal_return",
        "median_path_mae",
        "median_down_probability",
        "median_rebound_probability",
    )


def _naive_datetimes(values):
    converted = pd.to_datetime(values, errors="raise")
    if isinstance(converted, pd.Series):
        if converted.dt.tz is not None:
            return converted.dt.tz_convert(None)
        return converted
    index = pd.DatetimeIndex(converted)
    if index.tz is not None:
        index = index.tz_convert(None)
    return index
