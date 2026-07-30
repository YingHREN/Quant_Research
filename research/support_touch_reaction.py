"""Point-in-time first-touch reaction labels for frozen support zones."""

from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
REQUIRED_SIGNAL_COLUMNS = ("variant", "eligible", "zone_lower", "zone_upper")
OUTPUT_COLUMNS = (
    "ticker",
    "observation_date",
    "variant",
    "waiting_horizon",
    "zone_lower",
    "zone_upper",
    "atr20",
    "observation_distance_atr",
    "distance_bin",
    "touch_status",
    "touch_type",
    "touch_date",
    "touch_delay_sessions",
    "reaction_label",
    "accepted",
    "failed",
    "ambiguous",
    "reclaim_delay_sessions",
    "maximum_rebound_atr",
    "maximum_penetration_atr",
    "close_change_from_touch",
    "touch_volume_ratio",
    "event_end_date",
)


def build_support_touch_reaction_rows(
    ticker: str,
    history: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    waiting_horizon: int,
    reaction_sessions: int = 3,
) -> pd.DataFrame:
    """Return mature first-touch support episodes."""
    _validate_inputs(
        history,
        signals,
        waiting_horizon=waiting_horizon,
        reaction_sessions=reaction_sessions,
    )
    frame = history.loc[:, REQUIRED_PRICE_COLUMNS].astype(float)
    mature_count = max(0, len(frame) - waiting_horizon - reaction_sessions + 1)
    if mature_count == 0:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    atr20 = _atr20(frame)
    volume_ma20 = frame["Volume"].rolling(20, min_periods=20).mean().shift(1)
    rows: list[dict[str, object]] = []
    normalized_ticker = str(ticker).strip().upper()
    previous_observation_position: int | None = None
    previous_center: float | None = None
    previous_atr: float | None = None
    active_until_position = -1
    for observation_position in range(mature_count):
        signal = signals.iloc[observation_position]
        if not isinstance(signal["eligible"], (bool, np.bool_)):
            continue
        if not bool(signal["eligible"]):
            continue
        lower = _finite_positive(signal["zone_lower"])
        upper = _finite_positive(signal["zone_upper"])
        observation_atr = _finite_positive(atr20.iloc[observation_position])
        if (
            lower is None
            or upper is None
            or lower > upper
            or observation_atr is None
        ):
            continue
        observation_close = float(frame["Close"].iloc[observation_position])
        if observation_close <= upper:
            continue
        distance_atr = (observation_close - upper) / observation_atr
        if distance_atr > 3.5:
            continue
        center = (lower + upper) / 2.0
        if observation_position <= active_until_position:
            continue
        if previous_observation_position is not None:
            moved = abs(center - float(previous_center)) >= (
                0.25 * float(previous_atr)
            )
            spaced = (
                observation_position - previous_observation_position >= 10
            )
            if not moved and not spaced:
                continue
        row = _build_episode_row(
            normalized_ticker,
            frame,
            signal,
            observation_position=observation_position,
            waiting_horizon=waiting_horizon,
            reaction_sessions=reaction_sessions,
            lower=lower,
            upper=upper,
            observation_atr=observation_atr,
            distance_atr=distance_atr,
            volume_ma20=volume_ma20,
        )
        rows.append(row)
        previous_observation_position = observation_position
        previous_center = center
        previous_atr = observation_atr
        active_until_position = int(frame.index.get_loc(row["event_end_date"]))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _build_episode_row(
    ticker: str,
    frame: pd.DataFrame,
    signal: pd.Series,
    *,
    observation_position: int,
    waiting_horizon: int,
    reaction_sessions: int,
    lower: float,
    upper: float,
    observation_atr: float,
    distance_atr: float,
    volume_ma20: pd.Series,
) -> dict[str, object]:
    waiting = frame.iloc[
        observation_position + 1 : observation_position + waiting_horizon + 1
    ]
    touch_offset: int | None = None
    touch_type: str | None = None
    for offset, (_, day) in enumerate(waiting.iterrows(), start=1):
        if float(day["High"]) < lower:
            touch_offset = offset
            touch_type = "gap_through"
            break
        if float(day["Low"]) <= upper and float(day["High"]) >= lower:
            touch_offset = offset
            touch_type = "intersection"
            break

    common = {
        "ticker": ticker,
        "observation_date": frame.index[observation_position],
        "variant": str(signal["variant"]),
        "waiting_horizon": waiting_horizon,
        "zone_lower": lower,
        "zone_upper": upper,
        "atr20": observation_atr,
        "observation_distance_atr": distance_atr,
        "distance_bin": _distance_bin(distance_atr),
    }
    if touch_offset is None:
        end_position = observation_position + waiting_horizon
        return {
            **common,
            "touch_status": "not_touched",
            "touch_type": None,
            "touch_date": pd.NaT,
            "touch_delay_sessions": None,
            "reaction_label": "not_touched",
            "accepted": False,
            "failed": False,
            "ambiguous": False,
            "reclaim_delay_sessions": None,
            "maximum_rebound_atr": None,
            "maximum_penetration_atr": None,
            "close_change_from_touch": None,
            "touch_volume_ratio": None,
            "event_end_date": frame.index[end_position],
        }

    touch_position = observation_position + touch_offset
    reaction = frame.iloc[
        touch_position : touch_position + reaction_sessions
    ]
    closes = reaction["Close"].to_numpy(dtype=float)
    below = closes < lower
    consecutive_below = bool(
        len(below) >= 2 and np.logical_and(below[:-1], below[1:]).any()
    )
    deep_failure = bool((closes < lower - 0.5 * observation_atr).any())
    lower_reclaimed = bool((closes >= lower).any())
    failed = bool(
        consecutive_below
        or deep_failure
        or (touch_type == "gap_through" and not lower_reclaimed)
    )
    upper_reclaims = np.flatnonzero(closes >= upper)
    accepted = bool(not failed and len(upper_reclaims))
    ambiguous = bool(not accepted and not failed)
    touch_close = float(reaction["Close"].iloc[0])
    touch_volume_average = _finite_positive(volume_ma20.iloc[touch_position])
    return {
        **common,
        "touch_status": "touched",
        "touch_type": touch_type,
        "touch_date": frame.index[touch_position],
        "touch_delay_sessions": touch_offset,
        "reaction_label": (
            "failed" if failed else "accepted" if accepted else "ambiguous"
        ),
        "accepted": accepted,
        "failed": failed,
        "ambiguous": ambiguous,
        "reclaim_delay_sessions": (
            None if not len(upper_reclaims) else int(upper_reclaims[0])
        ),
        "maximum_rebound_atr": max(
            0.0,
            (float(reaction["High"].max()) - upper) / observation_atr,
        ),
        "maximum_penetration_atr": max(
            0.0,
            (lower - float(reaction["Low"].min())) / observation_atr,
        ),
        "close_change_from_touch": (
            float(reaction["Close"].iloc[-1]) / touch_close - 1.0
        ),
        "touch_volume_ratio": (
            None
            if touch_volume_average is None
            else float(reaction["Volume"].iloc[0]) / touch_volume_average
        ),
        "event_end_date": reaction.index[-1],
    }


def _atr20(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["Close"].shift(1)
    true_range = pd.concat(
        (
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    return true_range.rolling(20, min_periods=20).mean()


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number) or number <= 0.0:
        return None
    return number


def _distance_bin(distance_atr: float) -> str:
    if distance_atr <= 0.5:
        return "0_0.5_atr"
    if distance_atr <= 1.0:
        return "0.5_1.0_atr"
    if distance_atr <= 2.0:
        return "1.0_2.0_atr"
    return "2.0_3.5_atr"


def _validate_inputs(
    history: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    waiting_horizon: int,
    reaction_sessions: int,
) -> None:
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    if not isinstance(signals, pd.DataFrame):
        raise TypeError("signals must be a DataFrame")
    missing_prices = [
        column for column in REQUIRED_PRICE_COLUMNS if column not in history
    ]
    if missing_prices:
        raise ValueError(
            f"history is missing required columns: {missing_prices}"
        )
    missing_signals = [
        column for column in REQUIRED_SIGNAL_COLUMNS if column not in signals
    ]
    if missing_signals:
        raise ValueError(
            f"signals are missing required columns: {missing_signals}"
        )
    if not history.index.equals(signals.index):
        raise ValueError("history and signals must align")
    if history.index.has_duplicates or not history.index.is_monotonic_increasing:
        raise ValueError("history dates must be unique and increasing")
    if not isinstance(waiting_horizon, int) or waiting_horizon <= 0:
        raise ValueError("waiting_horizon must be a positive integer")
    if not isinstance(reaction_sessions, int) or reaction_sessions <= 0:
        raise ValueError("reaction_sessions must be a positive integer")
    values = history.loc[:, REQUIRED_PRICE_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("history OHLCV values must be finite")
    if (values[:, :4] <= 0.0).any() or (values[:, 4] < 0.0).any():
        raise ValueError("history OHLC prices must be positive and volume nonnegative")
