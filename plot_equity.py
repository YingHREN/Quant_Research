"""画买卖引擎的净值曲线图: 策略 vs SPY买入持有。

用法: python plot_equity.py --start 2024-10-15 --out output/equity.png
"""
from __future__ import annotations

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")  # 无GUI后端
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine_bt import run_engine


def plot(res, out, title):
    ec = pd.DataFrame(res["equity_curve"], columns=["date", "equity"]).set_index("date")
    budget = res["budget"]
    strat = ec["equity"] / budget  # 归一化到1

    spy = res["spy"]
    spy_al = spy[(spy.index >= ec.index[0]) & (spy.index <= ec.index[-1])]["Close"]
    spy_norm = spy_al / spy_al.iloc[0]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8),
                                    gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(strat.index, strat.values, label="Strategy (buy/sell)", lw=2, color="#1d9bf0")
    ax1.plot(spy_norm.index, spy_norm.values, label="SPY buy&hold", lw=1.5,
             color="#888", ls="--")
    ax1.axhline(1.0, color="#ccc", lw=0.8)
    ax1.set_title(title, fontsize=13)
    ax1.set_ylabel("Normalized equity (start=1.0)")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)

    # 下方: 策略回撤
    roll_max = strat.cummax()
    dd = (strat / roll_max - 1) * 100
    ax2.fill_between(dd.index, dd.values, 0, color="#f87171", alpha=0.5)
    ax2.set_ylabel("Strategy drawdown %")
    ax2.grid(alpha=0.3)

    # 标注买卖点
    for tr in res["trades"]:
        d = pd.Timestamp(tr["exit_date"])
        if d in strat.index:
            color = "#4ade80" if tr["ret_pct"] > 0 else "#f87171"
            ax1.scatter([d], [strat.loc[d]], s=18, color=color, zorder=5)

    final_ret = (strat.iloc[-1] - 1) * 100
    spy_ret = (spy_norm.iloc[-1] - 1) * 100
    ax1.text(0.02, 0.95, f"Strategy: {final_ret:+.1f}%  |  SPY: {spy_ret:+.1f}%  |  "
             f"MaxDD: {dd.min():.1f}%",
             transform=ax1.transAxes, fontsize=10, va="top",
             bbox=dict(boxstyle="round", fc="white", alpha=0.8))

    plt.tight_layout()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    plt.savefig(out, dpi=120)
    print(f"图已保存: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=100000)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default="output/equity.png")
    ap.add_argument("--title", default="Buy/Sell Strategy vs SPY")
    args = ap.parse_args()
    tickers = ["NVDA", "MU", "AVGO", "AMD", "AAPL", "MSFT", "META", "GOOGL", "AMZN",
               "TSLA", "NFLX", "CRM", "NBIS", "PLTR", "SMCI", "ANET", "PANW", "NOW",
               "UBER", "SHOP"]
    res = run_engine(tickers, budget=args.budget, start=args.start, end=args.end)
    if res:
        plot(res, args.out, args.title)


if __name__ == "__main__":
    main()
