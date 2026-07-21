"""事件驱动买卖回测引擎：给定初始资金,逐日模拟真实买卖,输出账户曲线。

vs backtest.py(只测"买入后40天涨跌"),本引擎模拟完整交易生命周期:
- 逐交易日循环
- 每日先处理卖出(止损/止盈/移动止损/评分转弱/大盘防守),再处理买入
- 大盘择时: SPY 和 QQQ 都在50日均线上方才允许开新仓
- 仓位: 最多5只等权,单票≤20%
- 成本: 每笔0.1%(手续费+滑点)
- 输出: 账户净值曲线、总收益、最大回撤、胜率、每笔交易

用法: python engine_bt.py --budget 100000 --tickers NVDA AAPL MU ...
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.fetch import fetch
from factors.compute import compute_all_asof
from scoring.engine import evaluate

# ---- 策略参数(可调) ----
MAX_POSITIONS = 5        # 最多同时持仓数
MAX_WEIGHT = 0.20        # 单票上限
STOP_LOSS = 0.07         # 硬止损 -7%
TAKE_PROFIT = 0.25       # 止盈 +25%
TRAIL_STOP = 0.10        # 移动止损:从最高点回撤10%
BUY_SCORE = 55           # 买入评分门槛(甜蜜区下沿)
SELL_SCORE = 40          # 评分转弱卖出门槛
COST = 0.001             # 单边成本0.1%
WARMUP = 60              # 前60天只算因子不交易
REBALANCE_EVERY = 5      # 每5交易日做一次决策(降频,近实盘)


def market_ok(spy_slice, qqq_slice):
    """SPY 和 QQQ 都在50日均线上方 → 允许开新仓。"""
    def above_ma50(s):
        if s is None or len(s) < 50:
            return False
        c = s["Close"]
        return bool(c.iloc[-1] > c.rolling(50).mean().iloc[-1])
    return above_ma50(spy_slice) and above_ma50(qqq_slice)


def run_engine(tickers, budget=100000.0, verbose=True, start=None, end=None):
    # 预拉所有数据
    data = {}
    for t in tickers:
        sd = fetch(t)
        if sd.ok:
            data[t] = sd.history
    spy = fetch("SPY").history
    qqq = fetch("QQQ").history
    if verbose:
        print(f"成功加载 {len(data)}/{len(tickers)} 只票 + SPY + QQQ", file=sys.stderr)
    if not data or spy.empty or qqq.empty:
        print("数据不足", file=sys.stderr)
        return None

    # 统一交易日历(用SPY的日期),可选时段过滤
    full_cal = spy.index
    win = full_cal
    if start:
        win = win[win >= pd.Timestamp(start)]
    if end:
        win = win[win <= pd.Timestamp(end)]
    if len(win) == 0:
        print("时段内无交易日", file=sys.stderr)
        return None
    # 交易窗口在 full_cal 中的起止位置;起点至少 WARMUP 后
    lo = max(WARMUP, full_cal.get_indexer([win[0]])[0])
    hi = full_cal.get_indexer([win[-1]])[0] + 1
    calendar = full_cal

    cash = budget
    positions = {}  # ticker -> {shares, entry, high, entry_date}
    equity_curve = []   # (date, total_equity)
    trades = []         # 每笔平仓记录

    for i in range(lo, hi):
        today = calendar[i]

        # 当日各持仓市值(用当日收盘)
        def price_of(t):
            h = data.get(t)
            if h is None:
                return None
            sub = h[h.index <= today]
            return float(sub["Close"].iloc[-1]) if len(sub) else None

        # 更新持仓最高点(供移动止损)
        for t, pos in positions.items():
            px = price_of(t)
            if px and px > pos["high"]:
                pos["high"] = px

        # ---- 大盘状态 ----
        spy_s = spy[spy.index <= today]
        qqq_s = qqq[qqq.index <= today]
        mkt = market_ok(spy_s, qqq_s)

        # 只在决策日(每5天)或触发风控时动作;风控每天都查
        is_decision_day = (i - lo) % REBALANCE_EVERY == 0

        # ---- 1. 卖出检查(每天) ----
        for t in list(positions.keys()):
            pos = positions[t]
            px = price_of(t)
            if px is None:
                continue
            reason = None
            ret = px / pos["entry"] - 1
            # 硬止损
            if ret <= -STOP_LOSS:
                reason = "止损"
            # 止盈
            elif ret >= TAKE_PROFIT:
                reason = "止盈"
            # 移动止损
            elif px <= pos["high"] * (1 - TRAIL_STOP):
                reason = "移动止损"
            # 跌破20日均线
            else:
                h = data[t][data[t].index <= today]
                if len(h) >= 20 and px < h["Close"].rolling(20).mean().iloc[-1] * 0.98:
                    reason = "破20日线"
            # 大盘转弱 → 防守卖出
            if reason is None and not mkt:
                reason = "大盘防守"
            # 评分转弱(仅决策日算,省算力)
            if reason is None and is_decision_day:
                h = data[t][data[t].index <= today]
                if len(h) >= WARMUP:
                    bench = spy_s
                    f = compute_all_asof(t, h, bench, {})
                    r = evaluate(f, mkt, price_only=True)
                    if r.total < SELL_SCORE:
                        reason = "评分转弱"
            if reason:
                proceeds = pos["shares"] * px * (1 - COST)
                cash += proceeds
                trades.append({
                    "ticker": t, "entry_date": str(pos["entry_date"].date()),
                    "exit_date": str(today.date()), "entry": round(pos["entry"], 2),
                    "exit": round(px, 2), "ret_pct": round(ret * 100, 2),
                    "reason": reason,
                })
                del positions[t]

        # ---- 2. 买入检查(仅决策日 + 大盘OK + 有空位) ----
        if is_decision_day and mkt and len(positions) < MAX_POSITIONS:
            # 对所有未持仓的票算评分,选最高分补仓
            candidates = []
            for t, h_full in data.items():
                if t in positions:
                    continue
                h = h_full[h_full.index <= today]
                if len(h) < WARMUP:
                    continue
                f = compute_all_asof(t, h, spy_s, {})
                r = evaluate(f, mkt, price_only=True)
                if r.total >= BUY_SCORE:
                    candidates.append((t, r.total))
            candidates.sort(key=lambda x: -x[1])
            slots = MAX_POSITIONS - len(positions)
            total_equity_now = cash + sum(
                positions[t]["shares"] * (price_of(t) or positions[t]["entry"])
                for t in positions)
            for t, sc in candidates[:slots]:
                px = price_of(t)
                if px is None or px <= 0:
                    continue
                alloc = min(total_equity_now * MAX_WEIGHT, cash)
                if alloc < total_equity_now * 0.02:  # 现金太少不开
                    continue
                shares = int(alloc / (px * (1 + COST)))
                if shares <= 0:
                    continue
                cost_amt = shares * px * (1 + COST)
                cash -= cost_amt
                positions[t] = {"shares": shares, "entry": px, "high": px,
                                "entry_date": today}

        # ---- 3. 记录净值 ----
        equity = cash + sum(
            positions[t]["shares"] * (price_of(t) or positions[t]["entry"])
            for t in positions)
        equity_curve.append((today, equity))

    return {"equity_curve": equity_curve, "trades": trades, "final_cash": cash,
            "budget": budget, "spy": spy, "qqq": qqq, "open_positions": positions}


def report(res):
    if res is None:
        print("无结果"); return
    ec = pd.DataFrame(res["equity_curve"], columns=["date", "equity"]).set_index("date")
    budget = res["budget"]
    final = ec["equity"].iloc[-1]
    total_ret = final / budget - 1

    # 最大回撤
    roll_max = ec["equity"].cummax()
    dd = (ec["equity"] / roll_max - 1)
    max_dd = dd.min()

    # 日收益 → 年化夏普
    daily = ec["equity"].pct_change().dropna()
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0

    # 买入持有基准(SPY)
    spy = res["spy"]
    spy_al = spy[(spy.index >= ec.index[0]) & (spy.index <= ec.index[-1])]["Close"]
    spy_ret = spy_al.iloc[-1] / spy_al.iloc[0] - 1 if len(spy_al) else 0

    trades = res["trades"]
    wins = [t for t in trades if t["ret_pct"] > 0]

    print("\n" + "=" * 60)
    print("买卖回测结果")
    print("=" * 60)
    print(f"区间: {ec.index[0].date()} → {ec.index[-1].date()}")
    print(f"初始资金: ${budget:,.0f}  期末: ${final:,.0f}")
    print(f"总收益: {total_ret:+.1%}   (同期SPY买入持有: {spy_ret:+.1%})")
    print(f"最大回撤: {max_dd:.1%}   年化夏普: {sharpe:.2f}")
    print(f"完成交易: {len(trades)}笔  胜率: {len(wins)/len(trades):.1%}" if trades else "无完成交易")
    if trades:
        avg_win = np.mean([t["ret_pct"] for t in wins]) if wins else 0
        losses = [t for t in trades if t["ret_pct"] <= 0]
        avg_loss = np.mean([t["ret_pct"] for t in losses]) if losses else 0
        print(f"平均盈利: {avg_win:+.1f}%  平均亏损: {avg_loss:+.1f}%  "
              f"盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss else "")
        # 卖出原因分布
        from collections import Counter
        rc = Counter(t["reason"] for t in trades)
        print(f"卖出原因: " + "  ".join(f"{k}={v}" for k, v in rc.items()))
    print(f"期末持仓: {len(res['open_positions'])}只  现金: ${res['final_cash']:,.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=100000)
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--start", default=None, help="交易起始日 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="交易结束日 YYYY-MM-DD")
    args = ap.parse_args()
    tickers = args.tickers or ["NVDA", "MU", "AVGO", "AMD", "AAPL", "MSFT", "META",
                               "GOOGL", "AMZN", "TSLA", "NFLX", "CRM", "NBIS", "PLTR",
                               "SMCI", "ANET", "PANW", "NOW", "UBER", "SHOP"]
    res = run_engine(tickers, budget=args.budget, start=args.start, end=args.end)
    report(res)
    print("\n⚠️ 仅供研究,非投资建议。含幸存者偏差、基本面移除等局限。")


if __name__ == "__main__":
    main()
