"""Frozen dual-cohort runner for causal bottom-state evaluation."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from research.bottom_state import (
    BOTTOM_MODEL_VERSION,
    POSITIVE_STATES,
    build_bottom_state_rows,
)
from research.bottom_state_evaluation import (
    HORIZONS,
    bottom_evaluation_decision,
    build_bottom_transition_events,
    evaluate_bottom_events,
)
from research.bottom_state_replay import build_bottom_state_replay
from research.expanded_market_data import ExpandedMarketDataRepository
from research.run_expanded_walkforward_study import classify_study_groups
from research.run_historical_demand_support_study import (
    _groups_for_dates,
    group_assignment_causal_audit,
    load_group_assignment_intervals,
)
from research.run_support_touch_reaction_study import (
    assign_reaction_folds,
    latest_point_in_time_groups,
    select_touch_reaction_cohorts,
)
from web.market_groups import REFERENCE_TICKERS


BOTTOM_ABLATIONS = (
    "full",
    "no_location",
    "no_exhaustion",
    "no_demand",
    "no_structure",
    "no_environment",
)
DISABLED_COMPONENTS = {
    "full": frozenset(),
    "no_location": frozenset({"location"}),
    "no_exhaustion": frozenset({"exhaustion"}),
    "no_demand": frozenset({"demand"}),
    "no_structure": frozenset({"structure"}),
    "no_environment": frozenset({"environment"}),
}


def run_bottom_state_evaluation(
    histories: dict[str, pd.DataFrame],
    *,
    cohorts: dict[str, tuple[str, ...]],
    fallback_groups: dict[str, str],
    group_intervals: pd.DataFrame,
    asof: str,
    start: str = "2018-01-01",
    horizons: tuple[int, ...] = HORIZONS,
    n_folds: int = 5,
    minimum_sessions: int = 220,
    replay_builder=build_bottom_state_replay,
    state_builder=build_bottom_state_rows,
    progress=None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Run all frozen variants and return metrics, events, and manifest."""
    checked_asof = pd.Timestamp(asof).normalize()
    checked_start = pd.Timestamp(start).normalize()
    if checked_start > checked_asof:
        raise ValueError("start must not be after asof")
    if not isinstance(minimum_sessions, int) or minimum_sessions <= 0:
        raise ValueError("minimum_sessions must be a positive integer")
    if not cohorts:
        raise ValueError("cohorts must not be empty")
    normalized_cohorts = {
        str(name): tuple(str(ticker).strip().upper() for ticker in tickers)
        for name, tickers in cohorts.items()
    }
    flattened = [
        ticker
        for tickers in normalized_cohorts.values()
        for ticker in tickers
    ]
    if len(flattened) != len(set(flattened)):
        raise ValueError("cohorts must be disjoint")
    bounded_histories = {
        str(ticker).strip().upper(): history.loc[
            history.index <= checked_asof
        ].copy(deep=True)
        for ticker, history in histories.items()
        if isinstance(history, pd.DataFrame)
    }
    event_frames = []
    exclusions = []
    coverage_rows = []
    evidence_contract_passed = True
    total = len(flattened)
    completed = 0
    for cohort, tickers in normalized_cohorts.items():
        for ticker in tickers:
            history = bounded_histories.get(ticker)
            if not isinstance(history, pd.DataFrame) or history.empty:
                exclusions.append(
                    {
                        "cohort": cohort,
                        "ticker": ticker,
                        "reason": "missing_history",
                    }
                )
                completed += 1
                continue
            if len(history) < minimum_sessions:
                exclusions.append(
                    {
                        "cohort": cohort,
                        "ticker": ticker,
                        "reason": f"fewer_than_{minimum_sessions}_sessions",
                        "session_count": int(len(history)),
                    }
                )
                completed += 1
                continue
            evidence, full_states = replay_builder(
                ticker,
                bounded_histories,
            )
            if not (
                isinstance(evidence, pd.DataFrame)
                and isinstance(full_states, pd.DataFrame)
                and evidence.index.equals(history.index)
                and full_states.index.equals(history.index)
            ):
                evidence_contract_passed = False
                exclusions.append(
                    {
                        "cohort": cohort,
                        "ticker": ticker,
                        "reason": "replay_alignment_failed",
                    }
                )
                completed += 1
                continue
            for variant in BOTTOM_ABLATIONS:
                states = (
                    full_states
                    if variant == "full"
                    else state_builder(
                        history,
                        evidence,
                        disabled_components=DISABLED_COMPONENTS[variant],
                    )
                )
                events = build_bottom_transition_events(
                    ticker,
                    history,
                    states,
                    horizons=tuple(horizons),
                )
                if not events.empty:
                    events = events.loc[
                        pd.to_datetime(
                            events["observation_date"],
                            errors="raise",
                        ).ge(checked_start)
                    ].copy()
                    events["variant"] = variant
                    events["event_id"] = (
                        variant + ":" + events["event_id"].astype(str)
                    )
                    events["cohort"] = cohort
                    events["group"] = _groups_for_dates(
                        ticker,
                        events["observation_date"],
                        group_intervals,
                        fallback_groups,
                    ).to_numpy()
                    regime_lookup = evidence.get(
                        "market_regime_state",
                        pd.Series("unavailable", index=evidence.index),
                    )
                    event_dates = pd.to_datetime(
                        events["observation_date"],
                        errors="raise",
                    )
                    events["market_regime"] = (
                        event_dates.map(regime_lookup)
                        .fillna("unavailable")
                        .astype(str)
                    )
                    event_frames.append(events)
                _append_coverage(
                    coverage_rows,
                    cohort=cohort,
                    ticker=ticker,
                    variant=variant,
                    horizons=horizons,
                    events=events,
                )
            completed += 1
            if progress is not None:
                progress(completed, total, ticker)
    if not event_frames:
        raise ValueError("study produced no mature bottom-state rows")
    event_columns = list(
        dict.fromkeys(
            column
            for frame in event_frames
            for column in frame.columns
        )
    )
    outcomes = pd.concat(
        [frame.dropna(axis=1, how="all") for frame in event_frames],
        ignore_index=True,
        sort=False,
    ).reindex(columns=event_columns)
    outcomes = assign_reaction_folds(outcomes, n_folds=n_folds)
    outcomes = outcomes.sort_values(
        [
            "cohort",
            "ticker",
            "variant",
            "scope",
            "horizon",
            "observation_date",
            "event_id",
        ],
        kind="stable",
    ).reset_index(drop=True)
    performance = evaluate_bottom_events(outcomes)
    performance["row_type"] = "performance"
    coverage = pd.DataFrame(coverage_rows)
    coverage["row_type"] = "coverage"
    metrics = pd.concat(
        (performance, coverage),
        ignore_index=True,
        sort=False,
    )
    metrics = _sort_metrics(metrics)
    causal_audit_passed = group_assignment_causal_audit(
        group_intervals,
        flattened,
    )
    decision = bottom_evaluation_decision(
        performance,
        evidence_contract_passed=evidence_contract_passed,
        group_causal_audit_passed=causal_audit_passed,
        future_holdout_passed=False,
    )
    manifest = {
        "study_version": "bottom-state-causal-evaluation-v1",
        "bottom_model_version": BOTTOM_MODEL_VERSION,
        "asof": checked_asof.date().isoformat(),
        "start": checked_start.date().isoformat(),
        "cohorts": {
            cohort: {
                "requested_count": len(tickers),
                "evaluated_count": sum(
                    ticker not in {
                        exclusion["ticker"]
                        for exclusion in exclusions
                        if exclusion["cohort"] == cohort
                    }
                    for ticker in tickers
                ),
                "tickers": list(tickers),
            }
            for cohort, tickers in normalized_cohorts.items()
        },
        "variants": list(BOTTOM_ABLATIONS),
        "horizons": [int(value) for value in horizons],
        "scopes": ["all_transitions", "non_overlapping"],
        "folds": int(n_folds),
        "exclusions": exclusions,
        "coverage_records": coverage_rows,
        "event_counts": {
            "rows": int(len(outcomes)),
            "positive": int(
                outcomes["observation_state"].isin(POSITIVE_STATES).sum()
            ),
            "baseline": int(
                outcomes["event_role"].eq("baseline").sum()
            ),
        },
        "audits": {
            "evidence_contract_passed": bool(evidence_contract_passed),
            "group_causal_audit_passed": bool(causal_audit_passed),
            "future_holdout_passed": False,
        },
        "decision": decision,
        "authority": "advisory_only",
    }
    return metrics, outcomes, manifest


def _append_coverage(
    rows: list[dict[str, object]],
    *,
    cohort: str,
    ticker: str,
    variant: str,
    horizons: tuple[int, ...],
    events: pd.DataFrame,
) -> None:
    for horizon in horizons:
        for scope in ("all_transitions", "non_overlapping"):
            selected = events.loc[
                events["horizon"].eq(int(horizon))
                & events["scope"].eq(scope)
            ] if not events.empty else events
            positive_count = (
                0
                if selected.empty
                else int(
                    selected["observation_state"].isin(POSITIVE_STATES).sum()
                )
            )
            rows.append(
                {
                    "cohort": cohort,
                    "ticker": ticker,
                    "variant": variant,
                    "horizon": int(horizon),
                    "scope": scope,
                    "event_count": int(len(selected)),
                    "positive_event_count": positive_count,
                    "baseline_count": (
                        0
                        if selected.empty
                        else int(selected["event_role"].eq("baseline").sum())
                    ),
                    "status": (
                        "available"
                        if positive_count > 0
                        else "unavailable_no_positive_events"
                    ),
                }
            )


def _sort_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    order = [
        column
        for column in (
            "row_type",
            "cohort",
            "ticker",
            "variant",
            "horizon",
            "scope",
            "state_slice",
            "slice_dimension",
            "slice_value",
            "metric_scope",
        )
        if column in metrics
    ]
    return metrics.sort_values(order, kind="stable", na_position="last").reset_index(
        drop=True
    )


def render_bottom_evaluation_report(
    metrics: pd.DataFrame,
    manifest: dict[str, object],
) -> str:
    """Return a compact Chinese research report."""
    decision = manifest["decision"]
    performance = metrics.loc[metrics["row_type"].eq("performance")]
    target = performance.loc[
        performance["cohort"].eq("confirmation")
        & performance["variant"].eq("full")
        & performance["horizon"].eq(10)
        & performance["scope"].eq("non_overlapping")
        & performance["state_slice"].eq("early_states")
        & performance["slice_dimension"].eq("all")
        & performance["metric_scope"].eq("matched")
    ]
    if target.empty:
        target_summary = "确认队列暂无可用的 10 日一对一匹配结果。"
    else:
        row = target.iloc[0]
        target_summary = (
            f"确认队列 10 日匹配事件 {int(row['matched_count'])} 个；"
            f"上涨率增量 {_percent(row['positive_rate_gain'])}；"
            f"平均收益增量 {_percent(row['return_gain'])}；"
            f"MAE 差值 {_percent(row['mae_delta'])}。"
        )
    zero_variants = sorted(
        set(
            metrics.loc[
                metrics["row_type"].eq("coverage")
                & metrics["positive_event_count"].fillna(0).eq(0),
                "variant",
            ].dropna()
        )
    )
    reasons = "、".join(decision["reasons"]) or "无"
    return "\n".join(
        (
            "# 底部状态因果评估",
            "",
            "> 仅供研究；不构成投资建议，不改变线上决策权限。",
            "",
            "## 确认队列核心结果",
            "",
            target_summary,
            "",
            "## 消融与覆盖",
            "",
            f"评估变体：{'、'.join(manifest['variants'])}。",
            f"存在零正向事件的变体：{'、'.join(zero_variants) or '无'}。",
            "",
            "## 研究门槛",
            "",
            f"当前可晋级：{'是' if decision['eligible'] else '否'}。",
            f"失败关闭原因：{reasons}。",
            "",
            "历史板块回填仍可能包含非因果假设；未来时间外留出通过前，"
            "模型始终保持 advisory_only。",
            "",
        )
    )


def write_bottom_evaluation_outputs(
    metrics: pd.DataFrame,
    manifest: dict[str, object],
    *,
    report_path: str | Path,
    metrics_path: str | Path,
    manifest_path: str | Path,
) -> None:
    """Write deterministic Markdown, CSV, and strict JSON artifacts."""
    paths = tuple(
        Path(value)
        for value in (report_path, metrics_path, manifest_path)
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    paths[0].write_text(
        render_bottom_evaluation_report(metrics, manifest),
        encoding="utf-8",
    )
    metrics.to_csv(paths[1], index=False)
    paths[2].write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _percent(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number * 100.0:+.2f} pp" if math.isfinite(number) else "—"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--asof", default="2026-07-24")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--cohort-size", type=int, default=240)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument(
        "--report",
        default="reports/bottom-state-causal-evaluation.md",
    )
    parser.add_argument(
        "--metrics",
        default="reports/bottom-state-causal-evaluation.csv",
    )
    parser.add_argument(
        "--manifest",
        default="reports/bottom-state-causal-evaluation.json",
    )
    args = parser.parse_args(argv)
    repository = ExpandedMarketDataRepository(args.database)
    classifications = repository.load_classifications(asof=args.asof)
    fallback_groups = classify_study_groups(classifications)
    intervals = load_group_assignment_intervals(args.database)
    latest_groups = latest_point_in_time_groups(
        fallback_groups,
        intervals,
        asof=args.asof,
    )
    cohorts = select_touch_reaction_cohorts(
        latest_groups,
        cohort_size=args.cohort_size,
    )
    requested_tickers = tuple(
        sorted(
            set(REFERENCE_TICKERS).union(
                ticker
                for tickers in cohorts.values()
                for ticker in tickers
            )
        )
    )
    histories = repository.load_universe_histories(
        asof=args.asof,
        tickers=requested_tickers,
    )

    def progress(done, total, ticker):
        if done == 1 or done % 10 == 0 or done == total:
            print(f"[bottom-state] {done}/{total} {ticker}", flush=True)

    metrics, _, manifest = run_bottom_state_evaluation(
        histories,
        cohorts=cohorts,
        fallback_groups=fallback_groups,
        group_intervals=intervals,
        asof=args.asof,
        start=args.start,
        n_folds=args.folds,
        progress=progress,
    )
    write_bottom_evaluation_outputs(
        metrics,
        manifest,
        report_path=args.report,
        metrics_path=args.metrics,
        manifest_path=args.manifest,
    )
    print(
        json.dumps(
            {
                "study_version": manifest["study_version"],
                "asof": manifest["asof"],
                "cohorts": {
                    name: {
                        "requested_count": value["requested_count"],
                        "evaluated_count": value["evaluated_count"],
                    }
                    for name, value in manifest["cohorts"].items()
                },
                "event_counts": manifest["event_counts"],
                "audits": manifest["audits"],
                "decision": manifest["decision"],
                "outputs": {
                    "report": args.report,
                    "metrics": args.metrics,
                    "manifest": args.manifest,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
