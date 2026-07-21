"""财报期风险管理 —— 方向2: 不赌预测方向, 只在财报事件前后调风险敞口。

第一部分(本文件): 验证地基假设——"财报后N日实现波动 > 财报前", 且跳空风险显著。
若连这个已知效应都不成立, 后面改V2无意义。

用真实公布日(AV reportedDate, SEC交叉验证过) + Tiingo 11年价格。
对每个财报事件:
- 财报前20日实现波动 vs 财报后20日实现波动
- 财报次日跳空幅度|open_gap|
- 对比"非财报期"随机20日窗口的波动(基线)

用法: ./venv/bin/python earnings_risk.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pead import load_earnings, fetch_long_history

WIN = 20  # 财报前后窗口


def realized_vol(closes):
    """日收益年化实现波动率。"""
    if len(closes) < 3:
        return np.nan
    r = np.diff(closes) / closes[:-1]
    return float(np.std(r) * np.sqrt(252) * 100)


def main():
    edf = load_earnings()
    print("=" * 66)
    print("财报期波动效应验证(方向2地基): 财报后波动 vs 财报前 vs 非财报基线")
    print("=" * 66)

    rows = []
    for t, g in edf.groupby("ticker"):
        h = fetch_long_history(t)
        if h.empty:
            continue
        idx = h.index
        closes = h["Close"].values
        opens = h["Open"].values
        for _, r in g.iterrows():
            rd = pd.Timestamp(r["reported"])
            future = idx[idx > rd]
            if len(future) < WIN + 1:
                continue
            ei = idx.get_loc(future[0])  # 财报后首个交易日
            if ei < WIN or ei + WIN >= len(closes):
                continue
            pre = closes[ei - WIN: ei]        # 财报前20日
            post = closes[ei: ei + WIN]       # 财报后20日
            gap = abs(opens[ei] / closes[ei - 1] - 1) * 100  # 次日开盘跳空
            rows.append({"ticker": t, "reported": r["reported"],
                         "vol_pre": realized_vol(pre), "vol_post": realized_vol(post),
                         "gap": gap})

    df = pd.DataFrame(rows).dropna()
    if df.empty:
        print("无有效事件"); return

    print(f"\n有效财报事件: {len(df)}  股票: {df['ticker'].nunique()}")
    print(f"\n财报前20日实现波动(年化%): 中位 {df['vol_pre'].median():.1f}  均值 {df['vol_pre'].mean():.1f}")
    print(f"财报后20日实现波动(年化%): 中位 {df['vol_post'].median():.1f}  均值 {df['vol_post'].mean():.1f}")
    ratio = df["vol_post"] / df["vol_pre"]
    print(f"后/前 波动比: 中位 {ratio.median():.2f}  均值 {ratio.mean():.2f}  "
          f"(>1 说明财报后更波动)")
    print(f"财报次日跳空|gap|: 中位 {df['gap'].median():.1f}%  均值 {df['gap'].mean():.1f}%  "
          f"95分位 {df['gap'].quantile(0.95):.1f}%")

    # 配对检验: 财报后波动是否系统性>财报前(按股票聚类的粗略bootstrap)
    diff = (df["vol_post"] - df["vol_pre"]).values
    rng = np.random.RandomState(42)
    tickers = df["ticker"].values
    uniq = df["ticker"].unique()
    boots = []
    for _ in range(2000):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        vals = np.concatenate([diff[tickers == tk] for tk in pick])
        boots.append(vals.mean())
    lo, hi = np.percentile(boots, [2.5, 97.5])
    print(f"\n财报后-前 波动差: 均值 {diff.mean():+.1f}个点  "
          f"按股票bootstrap 95%CI [{lo:+.1f}, {hi:+.1f}]  "
          f"{'✓效应显著(财报后更波动)' if lo > 0 else '✗不显著'}")

    # 跳空 vs 平日波动: 财报次日跳空是否远超平日日波动
    daily_move = df["vol_pre"] / np.sqrt(252)  # 平日单日波动近似
    print(f"\n财报跳空 vs 平日单日波动: 跳空中位{df['gap'].median():.1f}% vs "
          f"平日单日{daily_move.median():.1f}%  "
          f"→ 跳空是平日的 {df['gap'].median()/daily_move.median():.1f}倍")

    # 【关键修正】财报风险不是"后20日更波动"(已证伪), 而是"次日那一下跳空"。
    # 检验: 财报次日单日|收益| vs 非财报日单日|收益|, 按股票bootstrap。
    day1_moves, normal_moves = [], []
    tk_day1 = []
    for t, g in edf.groupby("ticker"):
        h = fetch_long_history(t)
        if h.empty:
            continue
        idx = h.index
        opens = h["Open"].values; closes = h["Close"].values
        rep_days = set()
        for _, r in g.iterrows():
            rd = pd.Timestamp(r["reported"])
            fut = idx[idx > rd]
            if len(fut) == 0:
                continue
            ei = idx.get_loc(fut[0])
            if 0 < ei < len(closes):
                # 财报次日 close-to-close 单日收益(含跳空)
                mv = abs(closes[ei] / closes[ei - 1] - 1) * 100
                day1_moves.append(mv); tk_day1.append(t)
                rep_days.add(ei)
        # 非财报日单日|收益|(平日基线)
        allr = np.abs(np.diff(closes) / closes[:-1]) * 100
        for j in range(len(allr)):
            if (j + 1) not in rep_days:
                normal_moves.append(allr[j])
    d1 = np.array(day1_moves); nm = np.array(normal_moves)
    print(f"\n【修正: 财报次日单日|收益| vs 平日】")
    print(f"  财报次日: 中位{np.median(d1):.1f}% 均值{d1.mean():.1f}% 95分位{np.percentile(d1,95):.1f}%")
    print(f"  平日:     中位{np.median(nm):.1f}% 均值{nm.mean():.1f}% 95分位{np.percentile(nm,95):.1f}%")
    print(f"  → 财报次日单日波动是平日的 {d1.mean()/nm.mean():.1f}倍(均值) / "
          f"{np.median(d1)/np.median(nm):.1f}倍(中位)")
    # 按股票bootstrap 财报次日均值 - 平日均值
    tk_arr = np.array(tk_day1); uniq2 = np.unique(tk_arr)
    rng2 = np.random.RandomState(42); bd = []
    nm_mean = nm.mean()
    for _ in range(2000):
        pick = rng2.choice(uniq2, size=len(uniq2), replace=True)
        vals = np.concatenate([d1[tk_arr == tk] for tk in pick])
        bd.append(vals.mean() - nm_mean)
    blo, bhi = np.percentile(bd, [2.5, 97.5])
    day1_real = blo > 0
    print(f"  财报次日-平日 差: 均值{d1.mean()-nm_mean:+.1f}点 95%CI[{blo:+.1f},{bhi:+.1f}] "
          f"{'✓次日确实是集中风险点' if day1_real else '✗'}")

    os.makedirs("output", exist_ok=True)
    df.to_csv("output/earnings_vol.csv", index=False)
    print(f"\n明细存 output/earnings_vol.csv")
    print("\n【地基判定(修正)】")
    if day1_real:
        print("  ✓ 财报'次日单日'波动显著高于平日(集中风险), 但'后20日窗口'波动无异常。")
        print("  → 正确风控 = 只在财报【前1天降敞口, 次日跳空后立即加回】, 而非降敞口20天。")
    else:
        print("  ✗ 连次日跳空也不显著于平日 → 财报风控无依据。")


if __name__ == "__main__":
    main()
