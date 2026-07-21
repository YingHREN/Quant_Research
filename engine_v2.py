"""引擎V2: 按 Codex 文献建议改造(核心+卫星 + 连续敞口 + 波动率目标)。

vs engine_bt.py(二元择时/满仓空仓/固定止盈)的改进:
1. 核心仓(默认50%)始终持有SPY → 解决V型反弹踏空
2. 卫星仓跑选股,敞口按大盘趋势强度"连续"调节(不是开关) → 平滑择时
3. 去掉固定25%止盈 → 让利润奔跑
4. 止损改ATR倍数(波动率标准化) → 适配不同波动股
5. 快进慢出: 大盘转弱时逐步减仓而非一次清空

用法: python engine_v2.py --budget 100000 --core 0.5
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.fetch import fetch
from factors.compute import compute_all_asof, _atr
from scoring.engine import evaluate

MAX_POSITIONS = 8         # 卫星仓最多持仓数(Codex建议10-20,免费数据下用8)
ATR_STOP_MULT = 2.5       # 止损 = 入场价 - 2.5×ATR
CHANDELIER_MULT = 3.0     # 吊灯退出 = 最高点 - 3×ATR(慢出)
BUY_SCORE = 55
COST = 0.001
WARMUP = 60
REBALANCE_EVERY = 5


def trend_strength(spy_s, qqq_s):
    """大盘趋势强度 → 卫星仓目标敞口(0.2~1.0),连续而非开关。

    综合 SPY/QQQ 相对50日/200日均线的位置。都强→满仓,都弱→最低20%。
    """
    def score(s):
        if s is None or len(s) < 200:
            if s is None or len(s) < 50:
                return 0.5
            c = s["Close"]
            return 1.0 if c.iloc[-1] > c.rolling(50).mean().iloc[-1] else 0.0
        c = s["Close"]
        ma50 = c.rolling(50).mean().iloc[-1]
        ma200 = c.rolling(200).mean().iloc[-1]
        px = c.iloc[-1]
        sc = 0.0
        if px > ma50: sc += 0.5
        if px > ma200: sc += 0.3
        if ma50 > ma200: sc += 0.2   # 均线多头排列
        return sc
    combined = (score(spy_s) + score(qqq_s)) / 2
    # 映射到 0.2~1.0
    return 0.2 + 0.8 * combined


def run_v2(tickers, budget=100000.0, core_frac=0.5, verbose=True, start=None, end=None,
           earnings_cal=None, pre_days=1, trim=0.5, rng=None):
    """rng: 传入 np.random.RandomState 则【随机选股】(从所有价格可得的票随机抽, 不看评分),
    用于消融——除"选哪只"外, 日期/持仓数/权重/止损/退出/敞口全与评分版一致。
    None=评分选股(原V2)。"""
    data = {}
    # 优先读本地库(不联网, 避免限流); 缺的才 fetch
    try:
        from build_local_db import load_local
    except Exception:
        load_local = lambda t: None
    for t in tickers:
        h = load_local(t) if load_local else None
        if h is not None and len(h):
            data[t] = h
        else:
            sd = fetch(t)
            if sd.ok:
                data[t] = sd.history
    spy = load_local("SPY")
    if spy is None or spy.empty:
        spy = fetch("SPY").history
    qqq = load_local("QQQ")
    if qqq is None or qqq.empty:
        qqq = fetch("QQQ").history
    if verbose:
        print(f"加载 {len(data)}/{len(tickers)} 票 + SPY + QQQ", file=sys.stderr)
    if not data or spy.empty or qqq.empty:
        print("数据不足", file=sys.stderr); return None

    full_cal = spy.index
    win = full_cal
    if start: win = win[win >= pd.Timestamp(start)]
    if end: win = win[win <= pd.Timestamp(end)]
    lo = max(WARMUP, full_cal.get_indexer([win[0]])[0])
    hi = full_cal.get_indexer([win[-1]])[0] + 1

    cash = budget
    core_shares = 0.0          # 核心仓SPY股数
    positions = {}             # 卫星仓
    equity_curve = []
    trades = []
    core_initialized = False

    def price_of(t, today):
        h = data.get(t)
        if h is None: return None
        sub = h[h.index <= today]
        return float(sub["Close"].iloc[-1]) if len(sub) else None

    def spy_price(today):
        sub = spy[spy.index <= today]
        return float(sub["Close"].iloc[-1]) if len(sub) else None

    for i in range(lo, hi):
        today = full_cal[i]
        spy_s = spy[spy.index <= today]
        qqq_s = qqq[qqq.index <= today]
        target_expo = trend_strength(spy_s, qqq_s)  # 卫星仓目标敞口
        is_dec = (i - lo) % REBALANCE_EVERY == 0

        # 初始化核心仓(始终持有,被动)
        if not core_initialized:
            sp = spy_price(today)
            if sp:
                core_budget = budget * core_frac
                core_shares = core_budget / (sp * (1 + COST))
                cash -= core_shares * sp * (1 + COST)
                core_initialized = True

        # 更新持仓最高点
        for t, pos in positions.items():
            px = price_of(t, today)
            if px and px > pos["high"]:
                pos["high"] = px

        # ---- 财报前降敞口(仅 earnings_cal 传入时) ----
        if earnings_cal is not None:
            # 未来 pre_days 个交易日内是否有该股财报公布日
            fwd_days = full_cal[i + 1: i + 1 + pre_days]
            for t in list(positions.keys()):
                pos = positions[t]
                cal = earnings_cal.get(t, set())
                earnings_ahead = any(d in cal for d in fwd_days)
                px = price_of(t, today)
                if px is None:
                    continue
                if earnings_ahead and not pos.get("trimmed"):
                    # 财报前: 卖出 trim 比例, 记住削减的股数以便恢复
                    sell_sh = pos["shares"] * trim
                    cash += sell_sh * px * (1 - COST)
                    pos["shares"] -= sell_sh
                    pos["trimmed"] = True
                    pos["trim_shares"] = sell_sh       # 削减掉的股数
                    pos["trim_restore_after"] = today
                elif pos.get("trimmed") and today > pos.get("trim_restore_after", today):
                    # 财报已过: 按削减掉的股数用当前价加回(等股数恢复)
                    want_sh = pos.get("trim_shares", 0.0)
                    cost_val = want_sh * px * (1 + COST)
                    if want_sh > 0 and cash >= cost_val:
                        pos["shares"] += want_sh
                        cash -= cost_val
                    pos["trimmed"] = False
                    pos["trim_shares"] = 0.0

        # ---- 卫星仓卖出(每天查) ----
        for t in list(positions.keys()):
            pos = positions[t]
            px = price_of(t, today)
            if px is None: continue
            h = data[t][data[t].index <= today]
            atr = _atr(h, 20) or px * 0.03
            reason = None
            # ATR止损
            if px <= pos["entry"] - ATR_STOP_MULT * atr:
                reason = "ATR止损"
            # 吊灯退出(慢出): 从最高点回撤3×ATR
            elif px <= pos["high"] - CHANDELIER_MULT * atr:
                reason = "吊灯退出"
            if reason:
                cash += pos["shares"] * px * (1 - COST)
                ret = px / pos["entry"] - 1
                trades.append({"ticker": t, "exit_date": str(today.date()),
                               "ret_pct": round(ret * 100, 2), "reason": reason})
                del positions[t]

        # ---- 卫星仓调仓(决策日) ----
        if is_dec:
            # 当前总权益
            total_eq = cash + core_shares * (spy_price(today) or 0) + sum(
                positions[t]["shares"] * (price_of(t, today) or positions[t]["entry"])
                for t in positions)
            satellite_budget = total_eq * (1 - core_frac) * target_expo  # 连续敞口
            cur_sat_value = sum(
                positions[t]["shares"] * (price_of(t, today) or positions[t]["entry"])
                for t in positions)

            # 若目标卫星敞口 < 当前(大盘转弱),逐步减仓(慢出:每次减1只最弱)
            if cur_sat_value > satellite_budget * 1.15 and positions:
                if rng is not None:
                    # 随机模式: 随机减一只(不用评分), 保证对照公平
                    worst = list(positions.keys())[rng.randint(0, len(positions))]
                else:
                    # 卖掉评分最低的一只
                    worst = None; worst_sc = 999
                    for t in positions:
                        h = data[t][data[t].index <= today]
                        if len(h) >= WARMUP:
                            f = compute_all_asof(t, h, spy_s, {})
                            sc = evaluate(f, True, price_only=True).total
                            if sc < worst_sc: worst_sc, worst = sc, t
                if worst:
                    px = price_of(worst, today)
                    cash += positions[worst]["shares"] * px * (1 - COST)
                    trades.append({"ticker": worst, "exit_date": str(today.date()),
                                   "ret_pct": round((px/positions[worst]["entry"]-1)*100, 2),
                                   "reason": "减仓(敞口)"})
                    del positions[worst]

            # 若有空间且目标敞口允许,买入(评分版:高分票; 随机版:随机抽)
            elif cur_sat_value < satellite_budget * 0.85 and len(positions) < MAX_POSITIONS:
                cands = []
                if rng is not None:
                    # 随机模式: 从所有价格可得(历史够)的非持仓票随机抽, 不看评分
                    avail = [t for t, hf in data.items()
                             if t not in positions and len(hf[hf.index <= today]) >= WARMUP]
                    rng.shuffle(avail)
                    cands = [(t, 0) for t in avail]
                else:
                    for t, hf in data.items():
                        if t in positions: continue
                        h = hf[hf.index <= today]
                        if len(h) < WARMUP: continue
                        f = compute_all_asof(t, h, spy_s, {})
                        r = evaluate(f, True, price_only=True)
                        if r.total >= BUY_SCORE:
                            cands.append((t, r.total))
                    cands.sort(key=lambda x: -x[1])
                slots = MAX_POSITIONS - len(positions)
                per = satellite_budget / MAX_POSITIONS
                for t, sc in cands[:slots]:
                    px = price_of(t, today)
                    if px and px > 0 and cash > per * 0.5:
                        alloc = min(per, cash)
                        shares = alloc / (px * (1 + COST))
                        if shares > 0:
                            cash -= shares * px * (1 + COST)
                            positions[t] = {"shares": shares, "entry": px, "high": px}

        # 记录净值
        eq = cash + core_shares * (spy_price(today) or 0) + sum(
            positions[t]["shares"] * (price_of(t, today) or positions[t]["entry"])
            for t in positions)
        equity_curve.append((today, eq))

    return {"equity_curve": equity_curve, "trades": trades, "budget": budget,
            "spy": spy, "core_frac": core_frac, "final_cash": cash,
            "open_positions": positions}


def report(res):
    if res is None: print("无结果"); return
    ec = pd.DataFrame(res["equity_curve"], columns=["date", "equity"]).set_index("date")
    budget = res["budget"]; final = ec["equity"].iloc[-1]
    total_ret = final / budget - 1
    roll_max = ec["equity"].cummax()
    max_dd = (ec["equity"] / roll_max - 1).min()
    daily = ec["equity"].pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    spy = res["spy"]
    spy_al = spy[(spy.index >= ec.index[0]) & (spy.index <= ec.index[-1])]["Close"]
    spy_ret = spy_al.iloc[-1] / spy_al.iloc[0] - 1 if len(spy_al) else 0
    spy_dd = (spy_al / spy_al.cummax() - 1).min() if len(spy_al) else 0
    trades = res["trades"]; wins = [t for t in trades if t["ret_pct"] > 0]

    print("\n" + "=" * 60)
    print(f"引擎V2 (核心{res['core_frac']:.0%}+卫星, 连续敞口)")
    print("=" * 60)
    print(f"区间: {ec.index[0].date()} → {ec.index[-1].date()}")
    print(f"初始: ${budget:,.0f}  期末: ${final:,.0f}")
    print(f"总收益: {total_ret:+.1%}  (SPY买入持有: {spy_ret:+.1%})")
    print(f"最大回撤: {max_dd:.1%}  (SPY: {spy_dd:.1%})  年化夏普: {sharpe:.2f}")
    if trades:
        print(f"卫星交易: {len(trades)}笔  胜率: {len(wins)/len(trades):.1%}")
        from collections import Counter
        print("卖出原因: " + "  ".join(f"{k}={v}" for k,v in Counter(t["reason"] for t in trades).items()))
    print(f"期末: 卫星持仓{len(res['open_positions'])}只  现金${res['final_cash']:,.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=100000)
    ap.add_argument("--core", type=float, default=0.5)
    ap.add_argument("--start", default=None); ap.add_argument("--end", default=None)
    ap.add_argument("--tickers", nargs="*")
    args = ap.parse_args()
    tickers = args.tickers or ["NVDA","MU","AVGO","AMD","AAPL","MSFT","META","GOOGL",
                               "AMZN","TSLA","NFLX","CRM","NBIS","PLTR","SMCI","ANET",
                               "PANW","NOW","UBER","SHOP"]
    res = run_v2(tickers, budget=args.budget, core_frac=args.core,
                 start=args.start, end=args.end)
    report(res)
    print("\n⚠️ 仅供研究,非投资建议。")


if __name__ == "__main__":
    main()
