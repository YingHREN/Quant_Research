"""Pure presentation contract for auditable model outputs."""

from __future__ import annotations

from collections.abc import Mapping

from web.contracts import json_safe


def build_model_outputs(forecast, chart_row, evaluation):
    """Group one point-in-time forecast into semantic model families."""
    forecast = _mapping(forecast)
    chart_row = _mapping(chart_row)
    evaluation = _mapping(evaluation)
    decision = _mapping(forecast.get("decision"))

    return {
        "primary": [_primary_output(forecast, evaluation)],
        "downside": [
            _immediate_risk(forecast),
            _remembered_risk(
                "bearish_turn_risk_rules_v2",
                decision.get("individual_risk_score"),
                decision,
                "model.individualRisk",
            ),
            _remembered_risk(
                "group_regime_risk_v1",
                decision.get("group_risk_score"),
                decision,
                "model.groupRisk",
            ),
            _remembered_risk(
                "slow_decline_risk_v1",
                decision.get("slow_decline_risk_score"),
                decision,
                "model.slowDecline",
            ),
            _planned("macro_risk", "remembered_state", "model.macroRisk"),
            _planned(
                "intraday_order_flow",
                "rule_score",
                "model.intradayOrderFlow",
                timing="intraday",
            ),
        ],
        "bullish_structure": [
            _structural_reversal(chart_row),
            _early_reversal(chart_row),
            _shape_state(
                chart_row,
                "strict_vcp",
                "strict_vcp",
                "model.strictVcp",
            ),
            _shape_state(
                chart_row,
                "tight_platform",
                "tight_platform",
                "model.tightPlatform",
            ),
            _planned(
                "demand_confirmation",
                "rule_score",
                "model.demandConfirmation",
            ),
        ],
        "decision": _decision_output(decision),
    }


def _primary_output(forecast, evaluation):
    available = forecast.get("predicted_return") is not None
    return {
        **_identity(
            forecast.get("model_key") or "ridge_direction_v1",
            forecast.get("model_version"),
            "statistical_forecast",
            "production",
            "available" if available else "unavailable",
            "next_session_open",
            "model.ridge",
        ),
        "horizon_sessions": forecast.get("horizon_sessions"),
        "predicted_return": json_safe(forecast.get("predicted_return")),
        "direction": forecast.get("raw_direction"),
        "training_sample_count": forecast.get("training_sample_count"),
        "training_cutoff": forecast.get("training_cutoff"),
        "confidence_status": forecast.get("confidence_status"),
        "confidence_reason": forecast.get("confidence_reason"),
        "evidence_status": evaluation.get("evidence_status", "not_precomputed"),
        "unavailable_reason": forecast.get("unavailable_reason"),
    }


def _immediate_risk(forecast):
    score = forecast.get("bearish_turn_score")
    available = score is not None
    return {
        **_identity(
            "bearish_turn_immediate_v1",
            "v1",
            "rule_score",
            "production",
            "active" if available and float(score) >= 70.0 else (
                "inactive" if available else "unavailable"
            ),
            "close_confirmed",
            "model.immediateRisk",
        ),
        "score": json_safe(score),
        "threshold": 70.0,
        "conditions": list(forecast.get("bearish_turn_conditions") or ()),
    }


def _remembered_risk(key, score, decision, translation_prefix):
    available = score is not None
    return {
        **_identity(
            key,
            "v1" if key != "bearish_turn_risk_rules_v2" else "v2",
            "remembered_state",
            "production",
            "active" if available and float(score) >= 20.0 else (
                "inactive" if available else "unavailable"
            ),
            "close_confirmed",
            translation_prefix,
        ),
        "score": json_safe(score),
        "state": (
            decision.get("persistent_risk_state") if available else "unavailable"
        ),
        "memory_age_sessions": (
            decision.get("persistent_risk_age_sessions") if available else None
        ),
    }


def _structural_reversal(row):
    count = row.get("reversal_signal_count")
    available = count is not None
    conditions = [
        key
        for key in (
            "prior_high_breakout",
            "trendline_breakout",
            "higher_low_confirmed",
        )
        if row.get(key) is True
    ]
    return {
        **_identity(
            "bullish_structure_reversal_v1",
            "v1",
            "rule_score",
            "production",
            "active" if row.get("reversal_candidate") is True else (
                "inactive" if available else "unavailable"
            ),
            "close_confirmed",
            "model.structuralReversal",
        ),
        "score": json_safe(count),
        "maximum_score": 3,
        "conditions": conditions,
    }


def _early_reversal(row):
    score = row.get("early_reversal_score")
    available = score is not None
    return {
        **_identity(
            "early_bullish_reversal_watch_v1",
            "v1",
            "rule_score",
            "production",
            "active" if row.get("early_reversal_watch") is True else (
                "inactive" if available else "unavailable"
            ),
            "close_confirmed",
            "model.earlyReversal",
        ),
        "score": json_safe(score),
        "maximum_score": 100,
        "conditions": list(row.get("early_reversal_conditions") or ()),
    }


def _shape_state(row, field, key, translation_prefix):
    if field not in row or row.get(field) is None:
        status = "unavailable"
        state = None
    else:
        state = bool(row.get(field))
        status = "active" if state else "inactive"
    return {
        **_identity(
            key,
            "v1",
            "shape_state",
            "production",
            status,
            "close_confirmed",
            translation_prefix,
        ),
        "state": state,
        "unavailable_reason": (
            "historical_shape_not_computed" if status == "unavailable" else None
        ),
    }


def _decision_output(decision):
    if not decision:
        return {
            **_identity(
                "forecast_decision_policy",
                "v2",
                "decision_policy",
                "production",
                "unavailable",
                "next_session_open",
                "model.decisionPolicy",
            ),
            "unavailable_reason": "decision_context_unavailable",
        }
    return {
        **_identity(
            decision.get("policy_key") or "forecast_decision_policy",
            decision.get("policy_version") or "v2",
            "decision_policy",
            "production",
            "available",
            "next_session_open",
            "model.decisionPolicy",
        ),
        "final_direction": decision.get("final_direction"),
        "risk_state": decision.get("risk_state"),
        "action": decision.get("action"),
        "reasons": list(decision.get("reasons") or ()),
    }


def _planned(key, kind, translation_prefix, *, timing="close_confirmed"):
    return {
        **_identity(
            key,
            None,
            kind,
            "planned",
            "unavailable",
            timing,
            translation_prefix,
        ),
        "unavailable_reason": "not_implemented",
    }


def _identity(key, version, kind, lifecycle, status, timing, translation_prefix):
    return {
        "key": key,
        "version": version,
        "kind": kind,
        "lifecycle": lifecycle,
        "status": status,
        "timing": timing,
        "name_key": f"{translation_prefix}.name",
        "explanation_key": f"{translation_prefix}.explanation",
        "limitation_key": f"{translation_prefix}.limitation",
    }


def _mapping(value):
    return value if isinstance(value, Mapping) else {}
