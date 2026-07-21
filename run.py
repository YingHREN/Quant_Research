"""CLI 主入口：批量对一组美股票评分，输出到终端表格 + CSV。

用法:
    python run.py AAPL NVDA MSFT
    python run.py --file tickers.txt
    python run.py --demo            # 用内置一组热门成长股
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime

from data.fetch import fetch, fetch_benchmark
from factors.compute import compute_all
from scoring.engine import evaluate

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

DEMO_TICKERS = ["NVDA", "AAPL", "MSFT", "META", "AVGO", "AMZN",
                "GOOGL", "TSLA", "AMD", "NFLX", "CRM", "PLTR"]


def market_uptrend(bench) -> bool:
    """大盘是否确认上升趋势：SPY 收盘 > 50 日均线 且 50 日均线上行。"""
    if bench is None or bench.empty or len(bench) < 60:
        return False
    c = bench["Close"]
    sma50 = c.rolling(50).mean()
    return bool(c.iloc[-1] > sma50.iloc[-1] and sma50.iloc[-1] > sma50.iloc[-10])


def run(tickers: list[str]) -> list:
    print(f"[1/3] 拉取大盘基准 SPY ...", file=sys.stderr)
    bench = fetch_benchmark()
    mkt_ok = market_uptrend(bench)
    print(f"      大盘状态: {'确认上升趋势 ✓' if mkt_ok else '未确认上升 ✗ (评分会扣大盘分)'}",
          file=sys.stderr)

    results = []
    for i, t in enumerate(tickers, 1):
        print(f"[2/3] ({i}/{len(tickers)}) 分析 {t} ...", file=sys.stderr)
        sd = fetch(t)
        if not sd.ok:
            print(f"      跳过 {t}: {sd.error}", file=sys.stderr)
            continue
        f = compute_all(sd, bench)
        r = evaluate(f, mkt_ok)
        results.append((r, f))

    results.sort(key=lambda x: x[0].total, reverse=True)
    return results, mkt_ok


def print_table(results):
    print("\n" + "=" * 92)
    print(f"{'排名':<4}{'代码':<8}{'总分':<7}{'评级':<14}{'过滤':<6}{'可买入':<8}{'RS':<7}{'距高%':<8}{'数据缺口'}")
    print("-" * 92)
    for rank, (r, f) in enumerate(results, 1):
        rs = f["rs"] if f["rs"] is not None else "-"
        pfh = f["hl52"]["pct_from_high"]
        pfh = f"{pfh:.1f}" if pfh is not None else "-"
        buy = "★是" if r.trigger["buyable_now"] else "否"
        pf = "✓" if r.passed_filter else "✗"
        gap = "; ".join(r.data_gaps) if r.data_gaps else ""
        print(f"{rank:<4}{r.ticker:<8}{r.total:<7}{r.grade:<14}{pf:<6}{buy:<8}{str(rs):<7}{pfh:<8}{gap}")
    print("=" * 92)


def print_detail(results):
    """打印前 3 名的评分明细。"""
    print("\n【前 3 名评分明细】")
    for r, f in results[:3]:
        print(f"\n▶ {r.ticker}  总分 {r.total}  {r.grade}")
        bd = "  ".join(f"{k}:{v}" for k, v in r.breakdown.items())
        print(f"  分项: {bd}")
        fund = f["fundamentals"]
        eps = fund.get("eps_yoy")
        rev = fund.get("rev_yoy")
        print(f"  基本面: EPS同比={eps if eps is None else round(eps,1)}%  营收同比={rev if rev is None else round(rev,1)}%")
        v = f["vcp"]
        print(f"  VCP: 收缩{v['n_contractions']}段 {v['contractions']} 递减={v['is_decreasing']} 量枯={v['vol_dryup']}")
        p = f["pivot"]
        print(f"  Pivot: {p['pivot']} 突破={p['breakout']} 量比={p.get('vol_ratio')} 距pivot={p.get('pct_over_pivot')}%")
        t = r.trigger
        print(f"  触发: VCP突破={t['vcp_breakout']} PocketPivot={t['pocket_pivot']} → 可买入={t['buyable_now']}")


def save_csv(results, mkt_ok):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"scores_{ts}.csv")
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp)
        w.writerow(["ticker", "total", "grade", "passed_filter", "buyable_now",
                    "rs", "eps_yoy", "rev_yoy", "pct_from_high", "vcp_contractions",
                    "pivot", "breakout", "vol_ratio", "data_gaps", "market_uptrend"])
        for r, f in results:
            fund = f["fundamentals"]
            p = f["pivot"]
            w.writerow([r.ticker, r.total, r.grade, r.passed_filter, r.trigger["buyable_now"],
                        f["rs"], fund.get("eps_yoy"), fund.get("rev_yoy"),
                        f["hl52"]["pct_from_high"], f["vcp"]["n_contractions"],
                        p["pivot"], p["breakout"], p.get("vol_ratio"),
                        "|".join(r.data_gaps), mkt_ok])
    print(f"\n[3/3] 结果已保存: {path}", file=sys.stderr)
    return path


def main():
    ap = argparse.ArgumentParser(description="美股 CAN SLIM + VCP 选股评分系统")
    ap.add_argument("tickers", nargs="*", help="股票代码，如 AAPL NVDA")
    ap.add_argument("--file", help="从文件读代码，每行一个")
    ap.add_argument("--demo", action="store_true", help="用内置示例股票")
    args = ap.parse_args()

    tickers = list(args.tickers)
    if args.file:
        with open(args.file) as fp:
            tickers += [line.strip() for line in fp if line.strip()]
    if args.demo or not tickers:
        tickers = DEMO_TICKERS

    results, mkt_ok = run(tickers)
    if not results:
        print("没有成功分析任何股票。", file=sys.stderr)
        sys.exit(1)
    print_table(results)
    print_detail(results)
    save_csv(results, mkt_ok)
    from data.fetch import PROVIDER
    print(f"\n⚠️  仅供研究，非投资建议。数据源={PROVIDER}；免费档下基本面/机构数据可能缺失，"
          f"52周高低可能为近似(短窗口)。")


if __name__ == "__main__":
    main()
