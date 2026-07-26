"""Pure presentation contract for auditable model outputs."""

from __future__ import annotations

from collections.abc import Mapping

from web.contracts import json_safe
from web.forecasts.model_output_registry import (
    ModelOutputDefinition,
    ModelOutputGroup,
    ModelOutputRegistry,
)


def build_model_outputs(forecast, chart_row, evaluation):
    """Group one point-in-time forecast into semantic model families."""
    forecast = _mapping(forecast)
    chart_row = _mapping(chart_row)
    evaluation = _mapping(evaluation)
    decision = _mapping(forecast.get("decision"))

    return default_model_output_registry().build(
        {
            "forecast": forecast,
            "chart_row": chart_row,
            "evaluation": evaluation,
            "decision": decision,
        }
    )


def default_model_output_registry():
    return _DEFAULT_MODEL_OUTPUT_REGISTRY


def _default_registry():
    registry = ModelOutputRegistry()
    for group in (
        ModelOutputGroup(
            "primary",
            "modelOutput.group.primary",
            10,
            "many",
        ),
        ModelOutputGroup(
            "downside",
            "modelOutput.group.downside",
            20,
            "many",
        ),
        ModelOutputGroup(
            "bullish_structure",
            "modelOutput.group.bullish",
            30,
            "many",
        ),
        ModelOutputGroup(
            "decision",
            "modelOutput.group.decision",
            40,
            "single",
        ),
    ):
        registry.register_group(group)

    def register(
        key,
        group,
        order,
        version,
        kind,
        lifecycle,
        timing,
        permission,
        translation_prefix,
        builder,
        *,
        version_resolver=None,
    ):
        registry.register_model(
            ModelOutputDefinition(
                key=key,
                group=group,
                order=order,
                version=version,
                kind=kind,
                lifecycle=lifecycle,
                timing=timing,
                decision_permission=permission,
                name_key=f"{translation_prefix}.name",
                explanation_key=f"{translation_prefix}.explanation",
                limitation_key=f"{translation_prefix}.limitation",
            ),
            builder,
            version_resolver=version_resolver,
        )

    register(
        "ridge_direction_v1",
        "primary",
        10,
        None,
        "statistical_forecast",
        "production",
        "next_session_open",
        "informational",
        "model.ridge",
        lambda context: _primary_output(
            context["forecast"],
            context["evaluation"],
        ),
        version_resolver=lambda context: context["forecast"].get(
            "model_version"
        ),
    )
    register(
        "bearish_turn_immediate_v1",
        "downside",
        10,
        "v1",
        "rule_score",
        "production",
        "close_confirmed",
        "veto_to_down",
        "model.immediateRisk",
        lambda context: _immediate_risk(context["forecast"]),
    )
    register(
        "bearish_turn_risk_rules_v2",
        "downside",
        20,
        "v2",
        "remembered_state",
        "production",
        "close_confirmed",
        "veto_to_down",
        "model.individualRisk",
        lambda context: _remembered_risk(
            "bearish_turn_risk_rules_v2",
            context["decision"].get("individual_risk_score"),
            context["decision"],
            "model.individualRisk",
        ),
    )
    register(
        "group_regime_risk_v1",
        "downside",
        30,
        "v1",
        "remembered_state",
        "production",
        "close_confirmed",
        "advisory",
        "model.groupRisk",
        lambda context: _remembered_risk(
            "group_regime_risk_v1",
            context["decision"].get("group_risk_score"),
            context["decision"],
            "model.groupRisk",
        ),
    )
    register(
        "slow_decline_risk_v1",
        "downside",
        40,
        "v1",
        "remembered_state",
        "production",
        "close_confirmed",
        "downgrade_to_neutral",
        "model.slowDecline",
        lambda context: _remembered_risk(
            "slow_decline_risk_v1",
            context["decision"].get("slow_decline_risk_score"),
            context["decision"],
            "model.slowDecline",
        ),
    )
    register(
        "high_level_distribution_risk_v1",
        "downside",
        50,
        "v1",
        "remembered_state",
        "production",
        "close_confirmed",
        "veto_to_down",
        "model.highLevelDistribution",
        lambda context: _high_level_distribution_risk(
            context["decision"]
        ),
    )
    register(
        "supply_pressure_v1",
        "downside",
        60,
        "v1",
        "rule_score",
        "production",
        "close_confirmed",
        "advisory",
        "model.supplyPressure",
        lambda context: _supply_pressure(context["chart_row"]),
    )
    register(
        "macro_risk",
        "downside",
        70,
        None,
        "remembered_state",
        "planned",
        "close_confirmed",
        "advisory",
        "model.macroRisk",
        lambda context: _planned(
            "macro_risk",
            "remembered_state",
            "model.macroRisk",
        ),
    )
    register(
        "intraday_order_flow",
        "downside",
        80,
        None,
        "rule_score",
        "planned",
        "intraday",
        "advisory",
        "model.intradayOrderFlow",
        lambda context: _planned(
            "intraday_order_flow",
            "rule_score",
            "model.intradayOrderFlow",
            timing="intraday",
        ),
    )
    register(
        "bullish_structure_reversal_v1",
        "bullish_structure",
        10,
        "v1",
        "rule_score",
        "production",
        "close_confirmed",
        "informational",
        "model.structuralReversal",
        lambda context: _structural_reversal(context["chart_row"]),
    )
    register(
        "early_bullish_reversal_watch_v1",
        "bullish_structure",
        20,
        "v1",
        "rule_score",
        "production",
        "close_confirmed",
        "advisory",
        "model.earlyReversal",
        lambda context: _early_reversal(context["chart_row"]),
    )
    for order, field, key, translation_prefix in (
        (30, "strict_vcp", "strict_vcp", "model.strictVcp"),
        (
            40,
            "tight_platform",
            "tight_platform",
            "model.tightPlatform",
        ),
    ):
        register(
            key,
            "bullish_structure",
            order,
            "v1",
            "shape_state",
            "production",
            "close_confirmed",
            "informational",
            translation_prefix,
            lambda context, shape=field, model_key=key, prefix=(
                translation_prefix
            ): _shape_state(
                context["chart_row"],
                shape,
                model_key,
                prefix,
            ),
        )
    register(
        "vcp_breakout_confirmed_v1",
        "bullish_structure",
        50,
        "v1",
        "rule_event",
        "production",
        "close_confirmed",
        "informational",
        "model.vcpBreakout",
        lambda context: _vcp_breakout(context["chart_row"]),
    )
    register(
        "pocket_pivot_v1",
        "bullish_structure",
        60,
        "v1",
        "rule_event",
        "production",
        "close_confirmed",
        "informational",
        "model.pocketPivot",
        lambda context: _pocket_pivot(context["chart_row"]),
    )
    register(
        "demand_confirmation_v1",
        "bullish_structure",
        70,
        "v1",
        "rule_score",
        "production",
        "close_confirmed",
        "advisory",
        "model.demandConfirmation",
        lambda context: _demand_confirmation(context["chart_row"]),
    )
    register(
        "forecast_decision_policy",
        "decision",
        10,
        "v2",
        "decision_policy",
        "production",
        "next_session_open",
        "final_policy",
        "model.decisionPolicy",
        lambda context: _decision_output(context["decision"]),
        version_resolver=lambda context: (
            context["decision"].get("policy_version") or "v2"
        ),
    )
    return registry


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
        "direction_accuracy": json_safe(evaluation.get("direction_accuracy")),
        "always_up_direction_accuracy": json_safe(
            evaluation.get("always_up_direction_accuracy")
        ),
        "balanced_accuracy": json_safe(evaluation.get("balanced_accuracy")),
        "macro_f1": json_safe(evaluation.get("macro_f1")),
        "non_overlapping_sample_count": evaluation.get(
            "non_overlapping_sample_count"
        ),
        "non_overlapping_direction_accuracy": json_safe(
            evaluation.get("non_overlapping_direction_accuracy")
        ),
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


def _high_level_distribution_risk(decision):
    score = decision.get("high_level_distribution_score")
    state = decision.get("high_level_distribution_state")
    available = score is not None and state not in (None, "unavailable")
    return {
        **_identity(
            "high_level_distribution_risk_v1",
            "v1",
            "remembered_state",
            "production",
            (
                "active"
                if available and state in {
                    "watch", "high", "confirmed", "fading",
                }
                else ("inactive" if available else "unavailable")
            ),
            "close_confirmed",
            "model.highLevelDistribution",
        ),
        "score": json_safe(score),
        "state": state if available else "unavailable",
        "raw_state": (
            decision.get("high_level_distribution_raw_state")
            if available
            else "unavailable"
        ),
        "memory_age_sessions": (
            decision.get("high_level_distribution_age_sessions")
            if available
            else None
        ),
        "high_level_context_score": json_safe(
            decision.get("high_level_context_score")
        ),
        "distribution_pressure_score": json_safe(
            decision.get("distribution_pressure_score")
        ),
        "structure_damage_score": json_safe(
            decision.get("structure_damage_score")
        ),
        "conditions": list(
            dict.fromkeys(
                (
                    *(
                        decision.get(
                            "high_level_distribution_conditions"
                        )
                        or ()
                    ),
                    *(decision.get("climax_run_conditions") or ()),
                    *(
                        decision.get("top_risk_recovery_conditions")
                        or ()
                    ),
                )
            )
        ),
        "distribution_count_5": decision.get("distribution_count_5"),
        "distribution_count_10": decision.get("distribution_count_10"),
        "distribution_count_20": decision.get("distribution_count_20"),
        "churning_count_10": decision.get("churning_count_10"),
        "churning_cluster": decision.get("churning_cluster"),
        "climax_run_score": json_safe(decision.get("climax_run_score")),
        "climax_run_candidate": decision.get("climax_run_candidate"),
        "risk_recovery": decision.get("top_risk_recovery"),
        "climax_run_conditions": list(
            decision.get("climax_run_conditions") or ()
        ),
        "unavailable_reason": (
            None if available else "insufficient_high_level_context"
        ),
    }


def _supply_pressure(row):
    return _supply_demand_score(
        row,
        key="supply_pressure_v1",
        score_field="supply_pressure_score",
        coverage_field="supply_pressure_coverage",
        conditions_field="supply_pressure_conditions",
        translation_prefix="model.supplyPressure",
        metric_definitions=(
            (
                "modelOutput.metric.closeVolumeSupply",
                "supply_close_volume_score",
            ),
            (
                "modelOutput.metric.rejectionSupply",
                "supply_rejection_score",
            ),
            (
                "modelOutput.metric.structureContextSupply",
                "supply_structure_context_score",
            ),
        ),
    )


def _demand_confirmation(row):
    return _supply_demand_score(
        row,
        key="demand_confirmation_v1",
        score_field="demand_confirmation_score",
        coverage_field="demand_confirmation_coverage",
        conditions_field="demand_confirmation_conditions",
        translation_prefix="model.demandConfirmation",
        metric_definitions=(
            (
                "modelOutput.metric.demandParticipation",
                "demand_participation_score",
            ),
            (
                "modelOutput.metric.demandAbsorption",
                "demand_absorption_score",
            ),
            (
                "modelOutput.metric.breakoutContextDemand",
                "demand_breakout_context_score",
            ),
        ),
    )


def _supply_demand_score(
    row,
    *,
    key,
    score_field,
    coverage_field,
    conditions_field,
    translation_prefix,
    metric_definitions,
):
    score = row.get(score_field)
    coverage = row.get(coverage_field)
    available = score is not None and coverage is not None
    return {
        **_identity(
            key,
            "v1",
            "rule_score",
            "production",
            (
                "active"
                if available and float(score) >= 50.0
                else ("inactive" if available else "unavailable")
            ),
            "close_confirmed",
            translation_prefix,
        ),
        "score": json_safe(score),
        "maximum_score": 100,
        "coverage": json_safe(coverage),
        "supply_demand_state": row.get(
            "supply_demand_state",
            "unavailable",
        ),
        "conditions": list(row.get(conditions_field) or ()),
        "unavailable_reason": (
            None
            if available
            else next(
                iter(row.get("unavailable_reasons") or ()),
                "model_data_unavailable",
            )
        ),
        "metrics": _metrics(
            row,
            tuple(
                (label_key, field, "score")
                for label_key, field in metric_definitions
            ),
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
    active_field = f"{field}_active"
    evidence = _mapping(row.get(f"{field}_evidence"))
    reason = (
        row.get(f"{field}_reject_reason")
        or evidence.get("reject_reason")
    )
    available = (
        evidence.get("available")
        if evidence.get("available") is not None
        else reason != "insufficient_history"
    )
    if active_field not in row or row.get(active_field) is None:
        status = "unavailable"
        state = None
        reason = reason or "historical_shape_not_computed"
    elif not available:
        status = "unavailable"
        state = False
    else:
        state = bool(row.get(active_field))
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
        "unavailable_reason": reason,
        "metrics": _shape_metrics(field, evidence),
    }


def _shape_metrics(field, evidence):
    if field == "strict_vcp":
        definitions = (
            ("modelOutput.metric.pivot", "vcp_pivot", "number"),
            ("modelOutput.metric.contractions", "n_contractions", "number"),
            (
                "modelOutput.metric.distanceToPivot",
                "distance_to_pivot_pct",
                "percent",
            ),
        )
    else:
        definitions = (
            ("modelOutput.metric.pivot", "platform_pivot", "number"),
            ("modelOutput.metric.rangePct", "range_pct", "percent"),
            (
                "modelOutput.metric.volumeDryupPct",
                "vol_dryup_pct",
                "percent",
            ),
        )
    return _metrics(evidence, definitions)


def _vcp_breakout(row):
    if "vcp_breakout_confirmed" not in row:
        status = "unavailable"
        reason = "historical_entry_signal_not_computed"
    else:
        status = (
            "active"
            if row.get("vcp_breakout_confirmed") is True
            else "inactive"
        )
        reason = row.get("vcp_breakout_reject_reason")
    values = {
        "pivot": row.get("vcp_breakout_pivot"),
        "volume_ratio": row.get("vcp_breakout_volume_ratio"),
        "required_volume_ratio": 1.4,
        "pct_over_pivot": row.get("vcp_breakout_pct_over_pivot"),
        "buy_zone_limit": 5.0,
    }
    return {
        **_identity(
            "vcp_breakout_confirmed_v1",
            "v1",
            "rule_event",
            "production",
            status,
            "close_confirmed",
            "model.vcpBreakout",
        ),
        "conditions": [
            key
            for field, key in (
                (
                    "vcp_breakout_price_confirmed",
                    "vcp_breakout_price_confirmed",
                ),
                (
                    "vcp_breakout_volume_confirmed",
                    "vcp_breakout_volume_confirmed",
                ),
                (
                    "vcp_breakout_buy_zone_confirmed",
                    "vcp_breakout_buy_zone_confirmed",
                ),
            )
            if row.get(field) is True
        ],
        "unavailable_reason": reason,
        "metrics": _metrics(
            values,
            (
                ("modelOutput.metric.pivot", "pivot", "number"),
                (
                    "modelOutput.metric.volumeRatio",
                    "volume_ratio",
                    "ratio",
                ),
                (
                    "modelOutput.metric.requiredVolumeRatio",
                    "required_volume_ratio",
                    "ratio",
                ),
                (
                    "modelOutput.metric.pctOverPivot",
                    "pct_over_pivot",
                    "percent",
                ),
                (
                    "modelOutput.metric.buyZoneLimit",
                    "buy_zone_limit",
                    "percent",
                ),
            ),
        ),
    }


def _pocket_pivot(row):
    evidence = _mapping(row.get("pocket_pivot_evidence"))
    reason = row.get("pocket_pivot_reject_reason") or evidence.get(
        "reject_reason"
    )
    if "pocket_pivot" not in row:
        status = "unavailable"
        reason = reason or "historical_entry_signal_not_computed"
    elif evidence.get("available") is False:
        status = "unavailable"
    else:
        status = "active" if row.get("pocket_pivot") is True else "inactive"
    values = {
        "current_volume": row.get(
            "pocket_pivot_current_volume",
            evidence.get("current_volume"),
        ),
        "prior_down_volume": row.get(
            "pocket_pivot_prior_down_volume",
            evidence.get("prior_down_volume"),
        ),
        "down_day_count": row.get(
            "pocket_pivot_down_day_count",
            evidence.get("down_day_count"),
        ),
        "lookback": evidence.get("lookback", 10),
    }
    return {
        **_identity(
            "pocket_pivot_v1",
            "v1",
            "rule_event",
            "production",
            status,
            "close_confirmed",
            "model.pocketPivot",
        ),
        "unavailable_reason": reason,
        "metrics": _metrics(
            values,
            (
                (
                    "modelOutput.metric.currentVolume",
                    "current_volume",
                    "volume",
                ),
                (
                    "modelOutput.metric.priorDownVolume",
                    "prior_down_volume",
                    "volume",
                ),
                (
                    "modelOutput.metric.downDayCount",
                    "down_day_count",
                    "number",
                ),
                (
                    "modelOutput.metric.lookbackSessions",
                    "lookback",
                    "number",
                ),
            ),
        ),
    }


def _metrics(values, definitions):
    return [
        {
            "label_key": label_key,
            "value": json_safe(values.get(field)),
            "format": format_name,
        }
        for label_key, field, format_name in definitions
        if values.get(field) is not None
    ]


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
    return {"status": status}


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


_DEFAULT_MODEL_OUTPUT_REGISTRY = _default_registry()
