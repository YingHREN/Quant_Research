"""Fixed walk-forward study for the pressure-regime downside specialist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.downside_specialist import (
    attach_next_open_mae_targets,
    downside_promotion_decision,
    evaluate_downside_predictions,
    walk_forward_downside_predictions,
)
from research.expanded_market_data import ExpandedMarketDataRepository
from research.market_direction_model import (
    walk_forward_direction_predictions,
    walk_forward_ridge_predictions,
)
from research.market_regime import build_market_regime_frame
from research.run_expanded_walkforward_study import (
    classify_study_groups,
    expanded_feature_sets,
    prepare_expanded_frame,
    select_analysis_tickers,
)
from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS
from web.market_groups import REFERENCE_TICKERS


SPECIALIST_FEATURE_COLUMNS = (
    *expanded_feature_sets(RIDGE_V4_FEATURE_COLUMNS)["ridge_decay_market"],
    "regime_is_correction",
    "regime_is_acute_selloff",
)
COMPARISON_SPECIFICATIONS = {
    "ridge_current": "ridge_down",
    "general_logistic": "general_logistic_down",
}


def build_matched_comparison_predictions(
    specialist_predictions,
    direction_predictions,
):
    """Align every comparator to the specialist's exact labeled test rows."""
    keys = ("ticker", "observation_date", "horizon", "fold")
    specialist = specialist_predictions.copy(deep=True)
    if specialist.duplicated(list(keys)).any():
        raise ValueError("specialist predictions contain duplicate test keys")
    base_columns = (
        *keys,
        "regime",
        "actual_event",
        "actual_mae",
    )
    missing = [
        column for column in (*base_columns, "predicted_event", "predicted_score")
        if column not in specialist
    ]
    if missing:
        raise ValueError(f"specialist predictions are missing columns: {missing}")
    output = [specialist.loc[:, (*base_columns, "specification", "predicted_event", "predicted_score")]]

    negative = specialist.loc[:, base_columns].copy()
    negative["specification"] = "negative_baseline"
    negative["predicted_event"] = False
    negative["predicted_score"] = 0.0
    output.append(negative)

    required_direction = {*keys, "specification", "predicted_direction"}
    missing_direction = sorted(
        required_direction.difference(direction_predictions.columns)
    )
    if missing_direction:
        raise ValueError(
            f"direction predictions are missing columns: {missing_direction}"
        )
    for source, target in COMPARISON_SPECIFICATIONS.items():
        selected = direction_predictions.loc[
            direction_predictions["specification"] == source,
            (*keys, "predicted_direction"),
        ].copy()
        if selected.duplicated(list(keys)).any():
            raise ValueError(f"{source} contains duplicate test keys")
        matched = specialist.loc[:, base_columns].merge(
            selected,
            on=list(keys),
            how="left",
            validate="one_to_one",
        )
        if matched["predicted_direction"].isna().any():
            raise ValueError(f"{source} is missing specialist test rows")
        matched["specification"] = target
        matched["predicted_event"] = (
            matched["predicted_direction"].astype(str) == "down"
        )
        matched["predicted_score"] = matched["predicted_event"].astype(float)
        output.append(
            matched.loc[
                :,
                (*base_columns, "specification", "predicted_event", "predicted_score"),
            ]
        )
    return pd.concat(output, ignore_index=True, sort=False)


def render_pressure_downside_report(metrics, manifest, rule_reference=None):
    """Render the frozen research conclusion and detailed metrics."""
    decision = manifest["decision"]
    decision_text = (
        "通过研究指标门槛，但仍不具备线上否决权"
        if decision["metric_gate_passed"]
        else "未通过研究指标门槛，不具备线上否决权"
    )
    specialist = metrics.loc[
        metrics["specification"] == "pressure_downside_logistic_v1"
    ].copy()
    columns = (
        "scope",
        "horizon",
        "regime_scope",
        "sample_mode",
        "sample_count",
        "event_rate",
        "precision",
        "recall",
        "specificity",
        "balanced_accuracy",
        "roc_auc",
        "pr_auc",
        "brier_score",
        "comparable_fold_count",
        "fold_win_rate_vs_ridge_down",
    )
    specialist = specialist.loc[:, [column for column in columns if column in specialist]]
    rule_text = (
        "- 现有规则参考不可用。"
        if rule_reference is None or rule_reference.empty
        else (
            "- 现有 8 项/12 项/板块/持续阴跌规则沿用此前 38 只已建模股票"
            "统一报告，仅作不同覆盖范围的参考，不与 240 只结果冒充同池比较。"
        )
    )
    reasons = decision.get("reasons") or []
    reason_lines = (
        ["- 无指标失败原因。"]
        if not reasons
        else [f"- `{reason}`" for reason in reasons]
    )
    return "\n".join(
        (
            "# 市场压力阶段向下风险专家",
            "",
            f"- 数据截止：{manifest['latest_date']}",
            f"- 研究股票：{manifest['ticker_count']} 只",
            f"- 点时样本：{manifest['row_count']:,}",
            f"- 结论：{decision_text}。",
            "- 标签：观察日收盘后生成信号，次日开盘进入；五日/二十日"
            "路径最大不利波动分别达到 −5%/−10%。",
            "- 触发范围：市场承压、修正和急跌；上涨趋势与震荡不适用。",
            "- 分数是未校准 Logistic 原始分数，不展示为真实概率。",
            "",
            "## 与现有规则的边界",
            "",
            rule_text,
            "",
            "## 晋级失败原因",
            "",
            *reason_lines,
            "",
            "## 专家分层指标",
            "",
            _markdown_table(specialist),
            "",
            "## 限制",
            "",
            "- Ridge 和通用 Logistic 没有路径风险概率，其 ROC/PR AUC 只使用"
            "二元“是否预测下跌”分数。",
            "- 当前股票池和 SEC 分类存在幸存者/历史快照限制，即使指标通过也"
            "不能直接赋予线上否决权。",
            "",
        )
    )


def _markdown_table(frame):
    if frame.empty:
        return "_无成熟样本。_"
    display = frame.copy()
    for column in display.select_dtypes(include="number"):
        display[column] = display[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    columns = list(display.columns)
    rows = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for values in display.astype(str).itertuples(index=False, name=None):
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--max-tickers", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--minimum-samples", type=int, default=1_000)
    parser.add_argument("--minimum-fold-samples", type=int, default=30)
    parser.add_argument(
        "--metrics",
        default="reports/pressure-downside-specialist.csv",
    )
    parser.add_argument(
        "--report",
        default="reports/pressure-downside-specialist.md",
    )
    parser.add_argument(
        "--manifest",
        default="reports/pressure-downside-specialist.json",
    )
    parser.add_argument(
        "--rule-reference",
        default="reports/expanded-risk-5d.csv",
    )
    args = parser.parse_args(argv)

    repository = ExpandedMarketDataRepository(args.database)
    classifications = repository.load_classifications()
    groups = classify_study_groups(classifications)
    tickers = select_analysis_tickers(
        groups,
        max_tickers=args.max_tickers,
        seed=args.seed,
    )
    requested = tuple(sorted(set(tickers).union(REFERENCE_TICKERS)))
    histories = repository.load_universe_histories(tickers=requested)
    frame = prepare_expanded_frame(
        histories,
        analysis_tickers=tickers,
        classifications=classifications,
        start_date=args.start,
        sector_mode="none",
    )
    frame = attach_next_open_mae_targets(
        frame,
        histories,
        horizons=(5, 20),
    )
    regimes = build_market_regime_frame(histories)
    observation_dates = frame.index.get_level_values("observation_date")
    frame["regime"] = observation_dates.map(regimes["regime"])
    frame["regime_is_correction"] = (
        frame["regime"] == "correction"
    ).astype(float)
    frame["regime_is_acute_selloff"] = (
        frame["regime"] == "acute_selloff"
    ).astype(float)

    matched_predictions = []
    full_direction_features = expanded_feature_sets(
        RIDGE_V4_FEATURE_COLUMNS
    )["ridge_decay_market"]
    for horizon in (5, 20):
        specialist = walk_forward_downside_predictions(
            frame,
            horizon=horizon,
            feature_columns=SPECIALIST_FEATURE_COLUMNS,
            n_folds=args.folds,
            minimum_samples=args.minimum_samples,
        )
        general = walk_forward_direction_predictions(
            frame,
            horizon=horizon,
            feature_sets={"general_logistic": full_direction_features},
            n_folds=args.folds,
            minimum_samples=args.minimum_samples,
        )
        ridge = walk_forward_ridge_predictions(
            frame,
            horizon=horizon,
            feature_columns=RIDGE_V4_FEATURE_COLUMNS,
            n_folds=args.folds,
            minimum_samples=args.minimum_samples,
            specification="ridge_current",
        )
        directions = pd.concat((general, ridge), ignore_index=True, sort=False)
        matched_predictions.append(
            build_matched_comparison_predictions(specialist, directions)
        )
    predictions = pd.concat(
        matched_predictions,
        ignore_index=True,
        sort=False,
    )
    metrics = evaluate_downside_predictions(
        predictions,
        group_map=groups,
        minimum_fold_samples=args.minimum_fold_samples,
    )
    decision = downside_promotion_decision(metrics)
    latest = frame.index.get_level_values("observation_date").max()
    manifest = {
        "study_version": "pressure_downside_logistic_v1",
        "label_version": "next_open_mae_v1",
        "latest_date": pd.Timestamp(latest).date().isoformat(),
        "start_date": args.start,
        "ticker_count": len(tickers),
        "row_count": len(frame),
        "folds": args.folds,
        "minimum_samples": args.minimum_samples,
        "minimum_fold_samples": args.minimum_fold_samples,
        "feature_count": len(SPECIALIST_FEATURE_COLUMNS),
        "prediction_rows": len(predictions),
        "decision": decision,
    }
    rule_path = Path(args.rule_reference)
    rule_reference = (
        pd.read_csv(rule_path) if rule_path.exists() else pd.DataFrame()
    )
    report = render_pressure_downside_report(
        metrics,
        manifest,
        rule_reference,
    )
    output_paths = (
        Path(args.metrics),
        Path(args.report),
        Path(args.manifest),
    )
    for path in output_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output_paths[0], index=False)
    output_paths[1].write_text(report, encoding="utf-8")
    output_paths[2].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
