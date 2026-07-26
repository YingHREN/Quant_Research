"""Causal historical VCP, breakout, platform, and Pocket Pivot rows."""

from __future__ import annotations

import hashlib
import math

import pandas as pd

from factors.compute import pocket_pivot_evidence, tight_platform
from research.vcp import detect_vcp, pattern_evidence


ENTRY_SIGNAL_VERSION = "historical-entry-signals-v1"
BREAKOUT_VOLUME_RATIO = 1.4
BREAKOUT_BUY_ZONE_PCT = 5.0
MAX_EVENT_LIFETIME = 60
_REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def build_entry_signal_rows(history: pd.DataFrame) -> list[dict]:
    """Build one point-in-time entry row per input trading session."""
    history = _validated_history(history)
    rows = []
    active_event = None
    seen_event_ids = set()
    prior_platform_active = False

    for position in range(len(history)):
        prefix = history.iloc[: position + 1]
        analysis_window = prefix.iloc[-252:]
        pocket_window = prefix.iloc[-12:]
        breakout_window = prefix.iloc[-51:]
        timestamp = pd.Timestamp(prefix.index[-1])
        current_close = float(prefix["Close"].iloc[-1])
        pattern = detect_vcp(analysis_window)
        strict_evidence = pattern_evidence(pattern)
        platform_evidence = _platform_evidence(
            tight_platform(analysis_window)
        )
        pocket_evidence = pocket_pivot_evidence(pocket_window)

        breakout = _breakout_evidence(
            breakout_window,
            active_event,
        )
        crossed = breakout["price_confirmed"]
        if crossed:
            active_event = None
        elif active_event is not None:
            age = position - active_event["first_position"]
            invalidation = active_event.get("invalidation_level")
            if (
                (
                    invalidation is not None
                    and current_close < invalidation
                )
                or pattern.reject_reason in {
                    "below_ma50",
                    "long_trend_not_rising",
                }
                or age >= MAX_EVENT_LIFETIME
            ):
                active_event = None

        strict_start = False
        if active_event is None and pattern.accepted and not crossed:
            identity = _event_id(strict_evidence)
            if identity not in seen_event_ids:
                active_event = {
                    "event_id": identity,
                    "pivot": float(pattern.pivot),
                    "first_position": position,
                    "invalidation_level": _invalidation_level(
                        strict_evidence
                    ),
                }
                seen_event_ids.add(identity)
                strict_start = True

        platform_active = bool(platform_evidence["active"])
        platform_start = platform_active and not prior_platform_active
        prior_platform_active = platform_active

        rows.append(
            {
                "time": timestamp.date().isoformat(),
                "strict_vcp_active": bool(pattern.accepted),
                "strict_vcp_start": strict_start,
                "strict_vcp_stage": pattern.stage,
                "strict_vcp_pivot": strict_evidence["vcp_pivot"],
                "strict_vcp_pivot_date": strict_evidence["pivot_date"],
                "strict_vcp_reject_reason": pattern.reject_reason,
                "strict_vcp_evidence": strict_evidence,
                "tight_platform_active": platform_active,
                "tight_platform_start": platform_start,
                "tight_platform_pivot": platform_evidence["platform_pivot"],
                "tight_platform_reject_reason": platform_evidence[
                    "reject_reason"
                ],
                "tight_platform_evidence": platform_evidence,
                "vcp_breakout_confirmed": breakout["confirmed"],
                "vcp_breakout_price_confirmed": breakout[
                    "price_confirmed"
                ],
                "vcp_breakout_volume_confirmed": breakout[
                    "volume_confirmed"
                ],
                "vcp_breakout_buy_zone_confirmed": breakout[
                    "buy_zone_confirmed"
                ],
                "vcp_breakout_pivot": breakout["pivot"],
                "vcp_breakout_volume_ratio": breakout["volume_ratio"],
                "vcp_breakout_pct_over_pivot": breakout["pct_over_pivot"],
                "vcp_breakout_reject_reason": breakout["reject_reason"],
                "pocket_pivot": bool(pocket_evidence["active"]),
                "pocket_pivot_current_volume": pocket_evidence[
                    "current_volume"
                ],
                "pocket_pivot_prior_down_volume": pocket_evidence[
                    "prior_down_volume"
                ],
                "pocket_pivot_down_day_count": pocket_evidence[
                    "down_day_count"
                ],
                "pocket_pivot_reject_reason": pocket_evidence[
                    "reject_reason"
                ],
                "pocket_pivot_evidence": pocket_evidence,
            }
        )
    return rows


def _validated_history(history):
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a pandas DataFrame")
    missing = [column for column in _REQUIRED_COLUMNS if column not in history]
    if missing:
        raise ValueError("history must contain OHLCV columns")
    if history.index.has_duplicates:
        raise ValueError("history index must be unique")
    history = history.sort_index()
    for column in _REQUIRED_COLUMNS:
        if not all(
            math.isfinite(float(value))
            for value in history[column].to_numpy()
        ):
            raise ValueError("history OHLCV values must be finite")
    return history


def _event_id(evidence):
    source = "{}|{:.2f}".format(
        evidence.get("base_start") or "",
        float(evidence.get("vcp_pivot") or 0.0),
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def _invalidation_level(evidence):
    legs = evidence.get("contraction_legs") or ()
    if legs:
        return float(legs[-1]["trough"])
    pivot = evidence.get("vcp_pivot")
    return None if pivot is None else float(pivot) * 0.95


def _breakout_evidence(prefix, active_event):
    pivot = (
        None
        if active_event is None
        else float(active_event["pivot"])
    )
    prior_volume = prefix["Volume"].astype(float).iloc[-51:-1]
    volume_ratio = (
        None
        if prior_volume.empty or float(prior_volume.mean()) == 0.0
        else float(prefix["Volume"].iloc[-1]) / float(prior_volume.mean())
    )
    if pivot is None or len(prefix) < 2:
        return {
            "confirmed": False,
            "price_confirmed": False,
            "volume_confirmed": False,
            "buy_zone_confirmed": False,
            "pivot": pivot,
            "volume_ratio": volume_ratio,
            "pct_over_pivot": None,
            "reject_reason": "no_prior_vcp_pivot",
        }

    previous_close = float(prefix["Close"].iloc[-2])
    current_close = float(prefix["Close"].iloc[-1])
    crossed = previous_close <= pivot < current_close
    pct_over = (
        (current_close / pivot - 1.0) * 100.0
        if crossed and pivot
        else None
    )
    volume_confirmed = bool(
        crossed
        and volume_ratio is not None
        and volume_ratio >= BREAKOUT_VOLUME_RATIO
    )
    buy_zone_confirmed = bool(
        crossed
        and pct_over is not None
        and 0.0 < pct_over <= BREAKOUT_BUY_ZONE_PCT
    )
    confirmed = crossed and volume_confirmed and buy_zone_confirmed
    if not crossed:
        reason = "pivot_not_crossed"
    elif not volume_confirmed:
        reason = "insufficient_breakout_volume"
    elif not buy_zone_confirmed:
        reason = "extended_above_buy_zone"
    else:
        reason = None
    return {
        "confirmed": confirmed,
        "price_confirmed": crossed,
        "volume_confirmed": volume_confirmed,
        "buy_zone_confirmed": buy_zone_confirmed,
        "pivot": pivot,
        "volume_ratio": volume_ratio,
        "pct_over_pivot": pct_over,
        "reject_reason": reason,
    }


def _platform_evidence(value):
    reason = value.get("reason")
    reason_codes = {
        "历史不足": "insufficient_history",
        "价未站上MA50": "below_ma50",
        "MA50<MA200": "ma50_below_ma200",
        "距52周高>10%": "too_far_from_52_week_high",
        "近20日涨幅>12%(加速上涨)": "accelerated_20_session_rise",
        "非横盘(净涨幅或效率比过高)": "not_sideways",
        "成交量未萎缩": "volume_not_dry",
    }
    if isinstance(reason, str) and reason.startswith("区间宽度"):
        reason_code = "platform_too_wide"
    else:
        reason_code = reason_codes.get(reason, reason)
    return {
        "available": reason_code != "insufficient_history",
        "active": bool(value.get("is_platform")),
        "platform_pivot": (
            None
            if value.get("platform_pivot") is None
            else float(value["platform_pivot"])
        ),
        "range_pct": (
            None
            if value.get("range_pct") is None
            else float(value["range_pct"])
        ),
        "vol_dryup_pct": (
            None
            if value.get("vol_dryup_pct") is None
            else float(value["vol_dryup_pct"])
        ),
        "reject_reason": reason_code,
    }
