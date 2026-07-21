"""财报前降敞口 —— V2 A/B 消融(方向2)。

地基已验证(earnings_risk.py): 财报次日单日波动是平日3倍(CI显著), 风险集中在次日跳空。
不预测方向, 只在持仓股财报前1天减仓一半, 次日跳空后加回。

A/B 消融(同一份engine_v2代码, 唯一区别是否传earnings_cal):
  A = 原V2
  B = V2 + 财报前降敞口
比 CAGR/最大回撤/Calmar/夏普。门槛(沿用V2消融惯例): 回撤改善>=10%或Calmar+15%, 且CAGR不降超1点。

⚠️只覆盖已抓财报日的股票池(11只与V2默认池重叠的大盘)。价格用V2的2年缓存→约2年窗口。

用法: ./venv/bin/python earnings_ablation.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine_v2 import run_v2
from data.fetch import fetch

EARN_DIR = os.path.join(os.path.dirname(__file__), "data", "cache", "earnings")

# V2默认池∩已抓财报日的股票
POOL = ["NVDA", "AVGO", "AMD", "AAPL", "MSFT", "META", "GOOGL", "AMZN",
        "TSLA", "NFLX", "CRM"]


def load_earnings_cal(tickers, trading_index):
    """{ticker: set(交易日Timestamp)}: 把AV reportedDate对齐到最近的交易日。"""
    cal = {}
    tset = pd.DatetimeIndex(trading_index)
    for t in tickers:
        path = os.path.join(EARN_DIR, f"{t}.json")
        if not os.path.exists(path):
            continue
        d = json.load(open(path))
        days = set()
        for r in d.get("quarterlyEarnings", []):
            rd = r.get("reportedDate")
            if not rd:
                continue
            ts = pd.Timestamp(rd)
            # 对齐到 >= reportedDate 的首个交易日(公布日或次日开盘所在交易日)
            fut = tset[tset >= ts]
            if len(fut):
                days.add(fut[0])
        cal[t] = days
    return cal


def metrics(res):
    ec = pd.DataFrame(res["equity_curve"], columns=["date", "equity"]).set_index("date")
    budget = res["budget"]; final = ec["equity"].iloc[-1]
    days = (ec.index[-1] - ec.index[0]).days
    yrs = max(days / 365.25, 0.1)
    cagr = (final / budget) ** (1 / yrs) - 1
    dd = (ec["equity"] / ec["equity"].cummax() - 1).min()
    daily = ec["equity"].pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    calmar = cagr / abs(dd) if dd != 0 else 0
    return {"cagr": cagr, "maxdd": dd, "sharpe": sharpe, "calmar": calmar,
            "final": final, "total_ret": final / budget - 1}


def main():
    spy = fetch("SPY").history
    trading_index = spy.index
    cal = load_earnings_cal(POOL, trading_index)
    n_events = sum(len(v) for v in cal.values())
    print("=" * 62)
    print(f"财报前降敞口 V2消融  池{len(POOL)}只  财报事件(窗口内){n_events}")
    print("=" * 62)

    print("\n跑 A=原V2 ...", file=sys.stderr)
    resA = run_v2(POOL, budget=100000, core_frac=0.5, verbose=False)
    print("跑 B=V2+财报前降敞口 ...", file=sys.stderr)
    resB = run_v2(POOL, budget=100000, core_frac=0.5, verbose=False,
                  earnings_cal=cal, pre_days=1, trim=0.5)

    mA, mB = metrics(resA), metrics(resB)
    print(f"\n{'指标':<12}{'A=原V2':>14}{'B=+财报风控':>16}{'变化':>12}")
    print("-" * 54)
    for k, name, pct in [("total_ret", "总收益", True), ("cagr", "CAGR", True),
                         ("maxdd", "最大回撤", True), ("sharpe", "夏普", False),
                         ("calmar", "Calmar", False)]:
        a, b = mA[k], mB[k]
        if pct:
            print(f"{name:<12}{a:>13.1%}{b:>15.1%}{(b-a)*100:>+10.1f}pt")
        else:
            print(f"{name:<12}{a:>13.2f}{b:>15.2f}{b-a:>+11.2f}")

    # 门槛判定
    dd_improve = (abs(mB["maxdd"]) - abs(mA["maxdd"])) / abs(mA["maxdd"]) * -1  # 正=回撤变小
    calmar_improve = (mB["calmar"] - mA["calmar"]) / abs(mA["calmar"]) if mA["calmar"] else 0
    cagr_drop = mA["cagr"] - mB["cagr"]
    print(f"\n回撤改善: {dd_improve:+.1%}  Calmar改善: {calmar_improve:+.1%}  "
          f"CAGR变化: {-cagr_drop*100:+.1f}pt")
    passed = (dd_improve >= 0.10 or calmar_improve >= 0.15) and cagr_drop <= 0.01
    print(f"\n【裁决】", "✓ 财报前降敞口改善V2风险调整收益, 值得进一步稳健性检验(参数扰动/regime/多相位)"
          if passed else "✗ 未达门槛(回撤改善>=10%或Calmar+15%且CAGR不降超1点)")
    print("注: 仅2年窗口/11只pilot。达标需再过参数扰动+分regime+多相位。")


if __name__ == "__main__":
    main()
