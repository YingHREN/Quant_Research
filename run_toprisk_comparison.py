"""Generate auditable JSON and Markdown TOPRISK comparison reports."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from research.evaluate_toprisk_comparison import (
    build_comparison_frame,
    evaluate_signals,
)
from web.services.market_data import MarketDataRepository


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-markdown", required=True)
    parser.add_argument("--adverse-threshold", type=float, default=-0.05)
    return parser


def _load_histories(database):
    return MarketDataRepository(database).load_universe_histories()


def _payload(frame, rows, adverse_threshold):
    dates = frame.index.get_level_values("observation_date")
    ridge_available = (
        "signal_ridge_down" in frame
        and frame["signal_ridge_down"].notna().any()
    )
    limitations = [
        "Signals are evaluated point-in-time against future path outcomes.",
        "Market-regime labels are unavailable in this report.",
    ]
    if not ridge_available:
        limitations.append(
            "Ridge historical forecasts are unavailable; Ridge-derived comparisons are marked unavailable."
        )
    return {
        "report_version": "toprisk_comparison_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "adverse_threshold": adverse_threshold,
        "horizons": [5, 10, 20],
        "coverage": {
            "start": None if len(dates) == 0 else dates.min().date().isoformat(),
            "end": None if len(dates) == 0 else dates.max().date().isoformat(),
            "row_count": len(frame),
            "ticker_count": len(
                set(frame.index.get_level_values("ticker"))
            ),
        },
        "model_versions": {
            "ridge": "ridge_direction_v1-v4",
            "immediate_8": "bearish_turn_immediate_v1",
            "memory_12": "bearish_turn_risk_rules_v2",
            "toprisk": "v1",
            "comparison": "toprisk_comparison_v1",
        },
        "market_regimes": {
            "status": "unavailable",
            "reason": "regime_labels_not_precomputed",
        },
        "limitations": limitations,
        "results": rows,
    }


def _format(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _markdown(payload):
    lines = [
        "# TOPRISK 统一比较报告",
        "",
        f"- 报告版本：`{payload['report_version']}`",
        f"- 样本区间：{payload['coverage']['start']} 至 {payload['coverage']['end']}",
        f"- 不利波动阈值：{payload['adverse_threshold']:.2%}",
        "",
        "## 结果",
        "",
        "| Group | Horizon | Signal | Status | N | Signals | Precision | Recall | Balanced accuracy | Mean MAE | Lead |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _format(row.get("group")),
                    _format(row.get("horizon_sessions")),
                    _format(row.get("signal")),
                    _format(row.get("status")),
                    _format(row.get("sample_count")),
                    _format(row.get("signal_count")),
                    _format(row.get("precision")),
                    _format(row.get("recall")),
                    _format(row.get("balanced_accuracy")),
                    _format(row.get("mean_mae")),
                    _format(row.get("mean_lead_sessions")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## 局限", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        histories = _load_histories(arguments.database)
        frame = build_comparison_frame(histories)
        rows = evaluate_signals(
            frame,
            horizons=(5, 10, 20),
            adverse_threshold=arguments.adverse_threshold,
        )
        payload = _payload(frame, rows, arguments.adverse_threshold)
        json_path = Path(arguments.output_json)
        markdown_path = Path(arguments.output_markdown)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(_markdown(payload), encoding="utf-8")
    except (OSError, RuntimeError, TypeError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
