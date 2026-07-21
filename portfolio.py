"""组合层策略回测：验证"只买top-K + 大盘过滤 + 止损"能否把实际收益转正。

即使单票胜率≈50%,好的组合构建+风控也可能产生正期望。
对回测样本(每个决策日有一批票的评分)模拟:
- 每个决策日,只在评分最高的 top-K 只里建仓(等权)
- 可选: 只在大盘上升趋势(market_ok=1)时开仓
- 用 fwd_ret 作为该笔持有收益
- 统计: 组合平均收益、胜率、夏普近似、vs 全市场基准

用法: python portfolio.py --data output/bt_p1.csv --topk 5
"""
from __future__ import annotations

import argparse
import numpy as np
import pandas as pd


def simulate(df, topk=5, market_filter=True, score_col="score", min_score=None):
    """按决策日选top-K建仓,返回每期组合收益序列。"""
    df = df.copy()
    if market_filter:
        df = df[df["market_ok"] == 1]
    if min_score is not None:
        df = df[df[score_col] >= min_score]

    period_rets = []      # 每个决策日的组合收益
    n_trades = 0
    wins = 0
    for d, g in df.groupby("date"):
        picks = g.nlargest(topk, score_col)
        if len(picks) == 0:
            continue
        ret = picks["fwd_ret"].mean()   # 等权
        period_rets.append(ret)
        n_trades += len(picks)
        wins += (picks["fwd_ret"] > 0).sum()
    return {
        "period_rets": np.array(period_rets),
        "n_periods": len(period_rets),
        "n_trades": n_trades,
        "trade_winrate": wins / n_trades if n_trades else 0,
    }


def report_strategy(res, label):
    r = res["period_rets"]
    if len(r) == 0:
        print(f"  {label}: 无交易"); return
    mean = r.mean()
    med = np.median(r)
    win_period = (r > 0).mean()
    sharpe = mean / r.std() if r.std() > 0 else 0  # 每期夏普(未年化)
    print(f"  {label}:")
    print(f"    决策期数={res['n_periods']}  笔数={res['n_trades']}  单笔胜率={res['trade_winrate']:.1%}")
    print(f"    组合每期平均收益={mean:+.2f}%  中位={med:+.2f}%  期胜率={win_period:.1%}  期夏普={sharpe:+.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="output/bt_p1.csv")
    ap.add_argument("--topk", type=int, default=5)
    args = ap.parse_args()
    df = pd.read_csv(args.data)
    df["actual_up"] = df["label"] == "上涨"
    print(f"样本 {len(df)}  股票 {df['ticker'].nunique()}  日期 {df['date'].nunique()}")

    # 基准: 全市场每期等权(相当于随便买)
    base_rets = df.groupby("date")["fwd_ret"].mean().values
    print(f"\n【基准】全市场等权: 每期平均={base_rets.mean():+.2f}%  期胜率={(base_rets>0).mean():.1%}")

    # 对比不同策略配置
    print("\n【策略对比】")
    for k in [3, 5, 10]:
        report_strategy(simulate(df, topk=k, market_filter=False), f"top{k} 无大盘过滤")
    print()
    for k in [3, 5, 10]:
        report_strategy(simulate(df, topk=k, market_filter=True), f"top{k} +大盘过滤")

    # 关键裁定: 最佳配置 vs 基准
    best = simulate(df, topk=5, market_filter=True)
    r = best["period_rets"]
    if len(r):
        edge = r.mean() - base_rets.mean()
        print(f"\n【裁定】top5+大盘过滤 每期收益 {r.mean():+.2f}% vs 基准 {base_rets.mean():+.2f}%  "
              f"超额={edge:+.2f}%  {'✓组合层有正边际' if edge > 0.5 else '✗无显著边际'}")


if __name__ == "__main__":
    main()
