"""随机选股消融 —— Codex裁定的终极验证: V2的收益是选股alpha还是敞口+退出机制?

方法(Codex): 完全匹配V2(日期/持仓数/权重/止损/退出/敞口), 只把"评分选股"换成
"随机选股", 跑N个种子, 看评分策略落在随机分布的百分位。
决策: 若评分策略没稳定进前5-10%, 正式承认增益来自敞口+退出, 非选股alpha。

⚠️幸存者偏差: 175只来自当前成分股, 结论须降级为"当前大盘股历史"。

用法: ./venv/bin/python random_ablation.py --seeds 200
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine_v2 import run_v2
from earnings_ablation import metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=200, help="随机种子数")
    ap.add_argument("--tickers", nargs="*")
    args = ap.parse_args()

    if args.tickers:
        pool = args.tickers
    else:
        from backtest import DEFAULT_UNIVERSE as U
        cached = set(os.path.basename(p).split("_")[0]
                     for p in glob.glob(os.path.join(os.path.dirname(__file__),
                                                     "data", "cache", "*tiingo*.pkl")))
        pool = [t for t in U if t in cached]

    print("=" * 64)
    print(f"随机选股消融  池{len(pool)}只  随机种子{args.seeds}个")
    print("⚠️ 幸存者偏差: 当前成分股回溯, 结论限'当前大盘股历史'")
    print("=" * 64)

    # 评分版(原V2)
    print("\n跑 评分选股V2 ...", file=sys.stderr)
    scored = metrics(run_v2(pool, budget=100000, core_frac=0.5, verbose=False))
    print(f"评分选股: CAGR {scored['cagr']:+.1%}  回撤 {scored['maxdd']:.1%}  "
          f"夏普 {scored['sharpe']:.2f}  总收益 {scored['total_ret']:+.1%}")

    # 随机版 N 个种子
    print(f"\n跑 {args.seeds}个随机选股种子 ...", file=sys.stderr)
    rand_cagr, rand_dd, rand_sharpe, rand_ret = [], [], [], []
    for s in range(args.seeds):
        rng = np.random.RandomState(s)
        m = metrics(run_v2(pool, budget=100000, core_frac=0.5, verbose=False, rng=rng))
        rand_cagr.append(m["cagr"]); rand_dd.append(m["maxdd"])
        rand_sharpe.append(m["sharpe"]); rand_ret.append(m["total_ret"])
        if (s + 1) % 50 == 0:
            print(f"  ...{s+1}/{args.seeds}", file=sys.stderr)

    rc = np.array(rand_cagr); rd = np.array(rand_dd); rs = np.array(rand_sharpe)
    print(f"\n随机选股分布({args.seeds}种子):")
    print(f"  CAGR:  中位{np.median(rc):+.1%}  5-95分位[{np.percentile(rc,5):+.1%}, {np.percentile(rc,95):+.1%}]")
    print(f"  回撤:  中位{np.median(rd):.1%}  5-95分位[{np.percentile(rd,5):.1%}, {np.percentile(rd,95):.1%}]")
    print(f"  夏普:  中位{np.median(rs):.2f}  5-95分位[{np.percentile(rs,5):.2f}, {np.percentile(rs,95):.2f}]")

    # 评分策略的百分位
    pct_cagr = (rc < scored["cagr"]).mean() * 100
    pct_sharpe = (rs < scored["sharpe"]).mean() * 100
    print(f"\n评分策略在随机分布中的百分位:")
    print(f"  CAGR: 超过{pct_cagr:.0f}%的随机组合")
    print(f"  夏普: 超过{pct_sharpe:.0f}%的随机组合")

    top = pct_cagr >= 90 and pct_sharpe >= 90
    print(f"\n【裁决】", end="")
    if top:
        print(f"评分策略稳定进前10%(CAGR{pct_cagr:.0f}%/夏普{pct_sharpe:.0f}%) → 选股可能有微弱edge, 需进一步确认")
    else:
        print(f"评分策略【未】稳定进前10%(CAGR{pct_cagr:.0f}%/夏普{pct_sharpe:.0f}%)")
        print("  → 正式承认: V2的收益来自'核心+卫星敞口+退出机制', 非选股alpha。")
        print("  评分仅保留为排序/解释界面, 删除选股alpha叙事。(与项目铁律一致)")


if __name__ == "__main__":
    main()
