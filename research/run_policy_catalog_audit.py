#!/usr/bin/env python3
"""Publish a descriptive coverage audit for the Fed policy catalog."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from import_policy_catalog import DEFAULT_CATALOG, import_catalog
from research.policy_period_returns import (
    POLICY_ETFS,
    describe_policy_periods,
)
from web.services.market_data import (
    MarketDataRepository,
    UnknownTicker,
)
from web.services.policy_event_store import PolicyEventStore


DEFAULT_PRICES = (
    Path(__file__).resolve().parents[1] / "data" / "prices.db"
)
DEFAULT_JSON = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "policy-catalog-audit.json"
)
DEFAULT_MARKDOWN = DEFAULT_JSON.with_suffix(".md")


def build_policy_catalog_audit(
    *,
    events,
    periods,
    histories,
    asof,
    catalog_version,
):
    results = describe_policy_periods(periods, histories, asof)
    event_frame = pd.DataFrame(events).copy()
    event_counts = (
        Counter(event_frame["event_type"].astype(str))
        if "event_type" in event_frame
        else Counter()
    )
    status_counts = Counter(results["status"].astype(str))
    etf_coverage = []
    for ticker in POLICY_ETFS:
        history = histories.get(ticker)
        scoped = results.loc[results["ticker"] == ticker]
        if history is None or history.empty:
            first_date = None
            last_date = None
            row_count = 0
        else:
            first_date = pd.Timestamp(history.index.min()).date().isoformat()
            last_date = pd.Timestamp(history.index.max()).date().isoformat()
            row_count = int(len(history))
        etf_coverage.append(
            {
                "ticker": ticker,
                "first_date": first_date,
                "last_date": last_date,
                "row_count": row_count,
                "period_status_counts": dict(
                    sorted(Counter(scoped["status"]).items())
                ),
            }
        )
    payload = {
        "task_key": "MACRO-ROTATION-001",
        "report_type": "descriptive_policy_audit",
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "asof": _utc_iso(asof),
        "catalog_version": str(catalog_version),
        "event_counts": dict(sorted(event_counts.items())),
        "period_coverage": {
            "periods": int(len(pd.DataFrame(periods))),
            "ticker_period_rows": int(len(results)),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "etf_coverage": etf_coverage,
        "period_results": _records(results),
        "limitations": [
            (
                "政策时期是人工历史描述，不产生预测分、板块排名建议或交易信号。"
            ),
            (
                "首批目录只覆盖已核实发布时间的事件；未收录事件保持覆盖缺口。"
            ),
            (
                "进行中时期、未上市 ETF 和本地历史缺失保持不可评估，不使用代理回填。"
            ),
        ],
    }
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return payload


def write_policy_catalog_audit(
    payload,
    *,
    json_path=DEFAULT_JSON,
    markdown_path=DEFAULT_MARKDOWN,
):
    json_target = Path(json_path)
    markdown_target = Path(markdown_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    markdown_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    markdown_target.write_text(
        _markdown(payload),
        encoding="utf-8",
    )


def run_policy_catalog_audit(
    *,
    catalog_path,
    prices_database,
    asof,
    json_path=DEFAULT_JSON,
    markdown_path=DEFAULT_MARKDOWN,
):
    with tempfile.TemporaryDirectory() as directory:
        policy_database = Path(directory) / "policy.db"
        summary = import_catalog(catalog_path, policy_database)
        store = PolicyEventStore(policy_database)
        events = store.load_events(asof)
        periods = store.load_periods(asof)
        histories = _load_histories(prices_database, asof)
        payload = build_policy_catalog_audit(
            events=events,
            periods=periods,
            histories=histories,
            asof=asof,
            catalog_version=summary["catalog_version"],
        )
    write_policy_catalog_audit(
        payload,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    return payload


def _load_histories(database, asof):
    repository = MarketDataRepository(database)
    histories = {}
    for ticker in POLICY_ETFS:
        try:
            histories[ticker] = repository.load_history(
                ticker,
                asof=asof,
            )
        except UnknownTicker:
            continue
    return histories


def _records(frame):
    records = []
    for raw in frame.to_dict(orient="records"):
        records.append(
            {
                str(key): _json_value(value)
                for key, value in raw.items()
            }
        )
    return records


def _json_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return value


def _utc_iso(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def _markdown(payload):
    coverage = payload["period_coverage"]
    event_lines = [
        f"- `{key}`：{value}"
        for key, value in payload["event_counts"].items()
    ] or ["- 暂无可用事件"]
    status_lines = [
        f"- `{key}`：{value}"
        for key, value in coverage["status_counts"].items()
    ]
    limitations = [
        f"- {value}"
        for value in payload["limitations"]
    ]
    return "\n".join(
        [
            "# 点时政策事件目录覆盖审计",
            "",
            f"- 任务：`{payload['task_key']}`",
            f"- 截止时点：`{payload['asof']}`",
            f"- 目录版本：`{payload['catalog_version']}`",
            "- 生命周期：`research`",
            "- 决策权限：`advisory`",
            "- 线上权力：`none`",
            "",
            "## 官方事件",
            "",
            *event_lines,
            "",
            "## 时期 × ETF 覆盖",
            "",
            f"- 时期数：{coverage['periods']}",
            f"- 明细行：{coverage['ticker_period_rows']}",
            *status_lines,
            "",
            "## 限制",
            "",
            *limitations,
            "",
        ]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit the descriptive Fed policy catalog coverage."
    )
    parser.add_argument("--catalog", default=os.fspath(DEFAULT_CATALOG))
    parser.add_argument("--prices", default=os.fspath(DEFAULT_PRICES))
    parser.add_argument("--asof", required=True)
    parser.add_argument("--json", default=os.fspath(DEFAULT_JSON))
    parser.add_argument("--markdown", default=os.fspath(DEFAULT_MARKDOWN))
    arguments = parser.parse_args(argv)
    payload = run_policy_catalog_audit(
        catalog_path=arguments.catalog,
        prices_database=arguments.prices,
        asof=arguments.asof,
        json_path=arguments.json,
        markdown_path=arguments.markdown,
    )
    print(
        json.dumps(
            {
                "task_key": payload["task_key"],
                "catalog_version": payload["catalog_version"],
                "periods": payload["period_coverage"]["periods"],
                "rows": payload["period_coverage"][
                    "ticker_period_rows"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
