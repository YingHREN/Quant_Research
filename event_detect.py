"""买卖点【事件检测】验证器 —— 阶段一 Go/No-Go(Codex 决策者设计的协议)。

核心纪律(Codex): 标签描述"未来经济结果", 检测器描述"当前结构", 两者必须分离。
绝不把 pocket_pivot/vcp/overheat 等检测条件写进标签(否则循环验证)。

目标: 在【今天选定的 watchlist 股票】历史日线上, 检测两类结构转换点:
  买点 = 基本面好的股被抛售后, 波动收缩企稳、机构疑似吸筹(VCP末端/pocket pivot)
  卖点 = 超买、机构疑似派发(overheat见顶)
并用"ATR三重障碍标签 + 同股同regime匹配基线"证明它比随机取点更准。

⚠️限制(Codex): Finnhub基本面无历史point-in-time, 故只能声明"在今天选定的watchlist
股票历史上验证量价事件", 不能声明"历史上先按当时基本面选股再获得这些结果"。

用法: ./venv/bin/python event_detect.py --n 20
"""
from __future__ import annotations

import argparse
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.fetch import fetch, fetch_benchmark
from factors.compute import vcp_analysis, overheat, pocket_pivot, _atr
from backtest import DEFAULT_UNIVERSE

# ============ 预注册配置(运行前冻结) ============
COOLDOWN = 10          # 同股同类信号冷却期(交易日), 连续信号合并成一个事件
MIN_HISTORY = 150      # 检测所需最少历史(VCP要150日lookback)
BUY_UP_ATR = 2.0       # 买点上障碍 = +2 ATR
BUY_DN_ATR = 1.0       # 买点下障碍 = -1 ATR
SELL_DN_ATR = 2.0      # 卖点下障碍 = -2 ATR
SELL_UP_ATR = 1.0      # 卖点上障碍 = +1 ATR
N_CONTROLS = 20        # 每事件匹配的控制点数
SEED = 42

# 检测器阈值(信号层, 与标签分离)
BUY_VCP_Q = 0.45       # vcp_quality 阈值
SELL_OH = 45.0         # overheat_score 阈值
TIGHT = False          # --tight: Codex允许的一次性收紧敏感性检查


# ============ 三重障碍标签(经济结果层, 绝不含检测条件) ============
def triple_barrier(future: pd.DataFrame, entry: float, atr: float,
                   up_atr: float, dn_atr: float) -> dict:
    """t+1 open 入场, 前向 future 日内先触上/下障碍定成败。

    up_atr/dn_atr: 上/下障碍各是几个 ATR。返回成败 + MAE/MFE/time-to-hit。
    同日双触: 保守算"下障碍先触"(失败方向), 避免日内路径不明的乐观偏差。
    """
    if future.empty or atr <= 0:
        return {"success": None}
    up_px = entry + up_atr * atr
    dn_px = entry - dn_atr * atr
    hi = future["High"].values
    lo = future["Low"].values
    close = future["Close"].values
    mae = 0.0  # 最大不利(相对entry, 负向, 以ATR计)
    mfe = 0.0  # 最大有利
    hit, hit_day = None, None
    for i in range(len(future)):
        mfe = max(mfe, (hi[i] - entry) / atr)
        mae = min(mae, (lo[i] - entry) / atr)
        touch_up = hi[i] >= up_px
        touch_dn = lo[i] <= dn_px
        if touch_up and touch_dn:
            hit, hit_day = "dn", i  # 同日双触→保守判下障碍
            break
        if touch_up:
            hit, hit_day = "up", i; break
        if touch_dn:
            hit, hit_day = "dn", i; break
    return {"hit": hit, "hit_day": hit_day, "mae_atr": round(mae, 2),
            "mfe_atr": round(mfe, 2),
            "ret_end": round((close[-1] - entry) / entry * 100, 2)}


def buy_label(future, entry, atr):
    """买点成功 = 20日内先触+2ATR且未先触-1ATR。"""
    r = triple_barrier(future, entry, atr, BUY_UP_ATR, BUY_DN_ATR)
    if r.get("hit") is None and "hit" not in r:
        return {"success": None}
    r["success"] = 1 if r.get("hit") == "up" else 0
    return r


def sell_label(future, entry, atr):
    """卖点成功 = 20日内先触-2ATR(先跌); 先涨+1ATR或到期=失败(不是真顶)。"""
    r = triple_barrier(future, entry, atr, SELL_UP_ATR, SELL_DN_ATR)
    if r.get("hit") is None and "hit" not in r:
        return {"success": None}
    r["success"] = 1 if r.get("hit") == "dn" else 0
    return r


# ============ 市场 regime(防牛市混淆的分层键) ============
def build_regime(bench: pd.DataFrame) -> pd.DataFrame:
    """给每个日期打 regime 标签: SPY vs MA200(升/降), SPY 20日波动率分桶。"""
    b = bench.copy()
    c = b["Close"]
    b["ma200"] = c.rolling(200, min_periods=50).mean()
    b["trend"] = np.where(c > b["ma200"], "up", "down")
    ret = c.pct_change()
    b["vol20"] = ret.rolling(20).std()
    med_vol = b["vol20"].median()
    b["volbucket"] = np.where(b["vol20"] > med_vol, "hivol", "lovol")
    return b[["trend", "vol20", "volbucket", "ma200"]]


# ============ 扫描: 检测器 + 标签 + 每日快照(供匹配控制点) ============
def scan_ticker(t: str, bench: pd.DataFrame, regime: pd.DataFrame, N: int):
    """返回 (events, snapshots)。
    events: 去重后的买/卖事件, 每个含标签成败。
    snapshots: 每个决策日的状态(距MA50/ATR分位/前期涨跌/regime), 供匹配控制点。
    """
    sd = fetch(t)
    if not sd.ok:
        return [], []
    h = sd.history
    n = len(h)
    if n < MIN_HISTORY + N + 2:
        return [], []

    events, snaps = [], []
    last_buy_idx, last_sell_idx = -999, -999
    ma50 = h["Close"].rolling(50).mean()
    for idx in range(MIN_HISTORY, n - N - 1):
        asof = h.index[idx]
        hist = h.iloc[:idx + 1]
        atr = _atr(hist, 20)
        if not atr or atr <= 0:
            continue
        price = float(hist["Close"].iloc[-1])
        # t+1 open 入场
        entry = float(h["Open"].iloc[idx + 1])
        future = h.iloc[idx + 1: idx + 1 + N]

        # --- 检测器(信号层) ---
        vcp = vcp_analysis(hist)
        oh = overheat(hist)
        pp = pocket_pivot(hist)
        vq = vcp.get("vcp_quality", 0)
        ohs = oh.get("overheat_score", 0)
        dma = (price / ma50.iloc[idx]) if not np.isnan(ma50.iloc[idx]) else None
        if TIGHT:
            # Codex 允许的一次性敏感性检查(冻结规则, 不再迭代)
            is_buy = (vq >= 0.60) and pp and (dma is not None and 0.97 <= dma <= 1.08)
            is_sell = (ohs >= 60) and (dma is not None and dma >= 1.12)
        else:
            is_buy = (vq >= BUY_VCP_Q) or pp
            is_sell = ohs >= SELL_OH

        # --- 快照(供匹配, 不含检测触发, 只含状态) ---
        dist_ma50 = (price / ma50.iloc[idx] - 1) * 100 if not np.isnan(ma50.iloc[idx]) else None
        run20 = oh.get("run20")
        reg = regime.reindex([asof]).iloc[0] if asof in regime.index else None
        snap = {
            "ticker": t, "date": asof, "idx": idx, "price": price, "atr": atr,
            "dist_ma50": dist_ma50, "run20": run20,
            "trend": reg["trend"] if reg is not None else None,
            "volbucket": reg["volbucket"] if reg is not None else None,
            "entry": entry,
        }
        # 预算买/卖标签(每个决策点都算一次, 供事件与控制点共用, 避免裁决时重算)
        blab = buy_label(future, entry, atr)
        slab = sell_label(future, entry, atr)
        snap["buy_success"] = blab.get("success")
        snap["sell_success"] = slab.get("success")
        snaps.append(snap)

        # --- 事件去重(冷却期) ---
        if is_buy and idx - last_buy_idx >= COOLDOWN:
            if blab.get("success") is not None:
                events.append({**snap, "kind": "buy", **blab})
                last_buy_idx = idx
        if is_sell and idx - last_sell_idx >= COOLDOWN:
            if slab.get("success") is not None:
                events.append({**snap, "kind": "sell", **slab})
                last_sell_idx = idx
    return events, snaps


def matched_control_precision(events_df, snaps_df, kind, rng):
    """每个事件匹配同股/相近日期/同regime/相近状态的控制点, 用【预算好的】标签成功率
    对比。用 (ticker,trend,volbucket) 预分组避免全表扫描。"""
    ev = events_df[events_df["kind"] == kind]
    if len(ev) == 0:
        return None
    ev_prec = ev["success"].mean()
    succ_col = "buy_success" if kind == "buy" else "sell_success"

    # 预分组: key=(ticker,trend,volbucket) → 该组的 idx/dist_ma50/success 数组
    groups = {}
    for key, g in snaps_df.groupby(["ticker", "trend", "volbucket"]):
        m = g[succ_col].notna()
        g = g[m]
        if len(g):
            groups[key] = (g["idx"].to_numpy(), g["dist_ma50"].to_numpy(dtype=float),
                           g[succ_col].to_numpy(dtype=float))

    ctrl_success = []
    for _, e in ev.iterrows():
        key = (e["ticker"], e["trend"], e["volbucket"])
        if key not in groups:
            continue
        idxs, dists, succ = groups[key]
        mask = (np.abs(idxs - e["idx"]) <= 60) & (idxs != e["idx"])
        if e["dist_ma50"] is not None and pd.notna(e["dist_ma50"]):
            mask &= np.abs(dists - e["dist_ma50"]) <= 8
        cand = succ[mask]
        cand = cand[~np.isnan(cand)]
        if len(cand) == 0:
            continue
        k = min(N_CONTROLS, len(cand))
        pick = rng.choice(cand, size=k, replace=len(cand) < N_CONTROLS)
        ctrl_success.extend(pick.tolist())
    ctrl_prec = float(np.mean(ctrl_success)) if ctrl_success else None
    return {"event_prec": float(ev_prec), "ctrl_prec": ctrl_prec,
            "n_events": len(ev), "n_controls": len(ctrl_success),
            "lift": ev_prec / ctrl_prec if ctrl_prec else None,
            "risk_diff": (ev_prec - ctrl_prec) if ctrl_prec is not None else None}


def block_bootstrap_lift(events_df, kind, n_boot=1000, seed=SEED):
    """按 股票+月份 成块 bootstrap 事件成功率(而非逐行), 给CI。
    用 groupby 预切成块列表, 避免每次boot全表过滤。"""
    ev = events_df[events_df["kind"] == kind].copy()
    if len(ev) < 10:
        return None
    ev["month"] = pd.to_datetime(ev["date"]).dt.to_period("M").astype(str)
    ev["block"] = ev["ticker"] + "_" + ev["month"]
    block_arrays = [g["success"].to_numpy() for _, g in ev.groupby("block")]
    nb = len(block_arrays)
    rng = np.random.RandomState(seed)
    means = []
    for _ in range(n_boot):
        pick = rng.randint(0, nb, size=nb)
        vals = np.concatenate([block_arrays[i] for i in pick])
        means.append(vals.mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="前向窗口N(主20, 稳健性10/40)")
    ap.add_argument("--tickers", nargs="*", default=DEFAULT_UNIVERSE)
    ap.add_argument("--save", default="output/events.csv")
    ap.add_argument("--tight", action="store_true",
                    help="Codex允许的一次性收紧敏感性检查(冻结规则,不再迭代)")
    args = ap.parse_args()
    N = args.n
    global TIGHT
    TIGHT = args.tight

    bench = fetch_benchmark()
    regime = build_regime(bench)
    print(f"配置冻结: N={N} 冷却={COOLDOWN}日 买障碍+{BUY_UP_ATR}/-{BUY_DN_ATR}ATR "
          f"卖障碍-{SELL_DN_ATR}/+{SELL_UP_ATR}ATR 检测器(vcpQ>={BUY_VCP_Q}或PP / OH>={SELL_OH})")

    all_events, all_snaps = [], []
    for i, t in enumerate(args.tickers):
        ev, sn = scan_ticker(t, bench, regime, N)
        if ev or sn:
            all_events.extend(ev)
            all_snaps.extend(sn)
        if (i + 1) % 30 == 0:
            print(f"  ...{i+1}/{len(args.tickers)}只, 累计事件{len(all_events)}", file=sys.stderr)

    ev_df = pd.DataFrame(all_events)
    sn_df = pd.DataFrame(all_snaps)
    print(f"扫描完成: {len(sn_df)}快照, {len(ev_df)}原始事件. 进入裁决...", file=sys.stderr)
    if ev_df.empty:
        print("无事件"); return
    os.makedirs("output", exist_ok=True)
    ev_df.to_csv(args.save, index=False)
    sn_df.to_pickle("output/event_snaps.pkl")

    rng = np.random.RandomState(SEED)
    print(f"\n{'='*70}\n阶段一 Go/No-Go 裁决 (N={N})\n{'='*70}")
    print(f"总快照(决策日): {len(sn_df)}  股票: {sn_df['ticker'].nunique()}")

    for kind, label_cn in [("buy", "买点(企稳吸筹)"), ("sell", "卖点(超买派发)")]:
        ev_k = ev_df[ev_df["kind"] == kind]
        print(f"\n【{label_cn}】")
        if len(ev_k) == 0:
            print("  无事件"); continue
        # 事件频率(每股每年)
        yrs = (pd.to_datetime(sn_df["date"]).max() - pd.to_datetime(sn_df["date"]).min()).days / 365.25
        per_stock_yr = len(ev_k) / max(1, ev_k["ticker"].nunique()) / max(0.1, yrs)
        m = matched_control_precision(ev_df, sn_df, kind, rng)
        ci = block_bootstrap_lift(ev_df, kind)
        print(f"  去重事件数: {len(ev_k)}  (涉及{ev_k['ticker'].nunique()}只, ~{per_stock_yr:.1f}次/股/年)")
        print(f"  事件成功率: {m['event_prec']:.1%}  |  匹配控制点成功率: "
              f"{m['ctrl_prec']:.1%} (n={m['n_controls']})" if m['ctrl_prec'] else "  控制点不足")
        if m['lift']:
            print(f"  Precision Lift: {m['lift']:.2f}x  |  风险差: {m['risk_diff']*100:+.1f}个点")
        print(f"  MAE/ATR中位: {ev_k['mae_atr'].median():.2f}  MFE/ATR中位: {ev_k['mfe_atr'].median():.2f}  "
              f"末端收益中位: {ev_k['ret_end'].median():+.2f}%")
        if ci:
            print(f"  成功率块bootstrap(股票+月份) 95%CI: [{ci[0]:.1%}, {ci[1]:.1%}]")

        # Go/No-Go 门槛
        gate = []
        gate.append(("事件>=100", len(ev_k) >= 100))
        gate.append(("lift>=1.30", m['lift'] is not None and m['lift'] >= 1.30))
        gate.append(("风险差>=10点", m['risk_diff'] is not None and m['risk_diff'] >= 0.10))
        gate.append(("频率1~6次/股/年", 1 <= per_stock_yr <= 6))
        print("  门槛: " + "  ".join(f"{'✓' if ok else '✗'}{name}" for name, ok in gate))
        verdict = "GO(初步)" if all(ok for _, ok in gate) else "NO-GO/证据不足"
        print(f"  ►► {label_cn} 阶段一: {verdict}")

    print(f"\n事件明细已存 {args.save}")
    print("注: 这是单次全样本扫描。regime/多相位/leave-one-sector-out 稳健性检验待过初步门槛后补。")


if __name__ == "__main__":
    main()
