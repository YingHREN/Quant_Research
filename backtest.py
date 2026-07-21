"""历史回测：检验评分/预测的准确率，并支持迭代调参。

方法(walk-forward + triple-barrier):
1. 对每只票，从最早可算因子的点开始，每隔 STEP 天取一个 as-of 决策点
2. 在该点用"截止当日"的数据算因子→打分→预测方向
3. 向后看 HORIZON 个交易日，用 triple-barrier 定实际标签:
   - 期间最高价先触及 +TARGET% 且未先跌破 -STOP% → 实际=上涨
   - 先跌破 -STOP% 或期末收益<0 → 实际=下跌
   - 其余 → 震荡
4. 统计: 方向命中率、看多样本的胜率、分数分档的实际上涨率(校准曲线)、
   评分与前向收益的相关性(IC)

用法:
    python backtest.py                 # 用内置universe跑
    python backtest.py --calibrate     # 额外输出logistic校准建议
"""
from __future__ import annotations

import argparse
import os
import sys
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.fetch import fetch, fetch_benchmark
from factors.compute import compute_all_asof
from scoring.engine import evaluate
from scoring.predict import predict, _base_direction

# 回测参数(可被迭代调整)
HORIZON = 40      # 前向持有交易日(~8周,主周期)
TARGET = 10.0     # 上涨目标 %
STOP = 7.0        # 止损 %
STEP = 5          # 每隔多少交易日取一个决策点
MIN_HISTORY = 60  # 算因子所需最少历史

CACHE_BT = os.path.join(os.path.dirname(__file__), "data", "cache", "bt_universe.pkl")


def market_ok_asof(bench_slice: pd.DataFrame) -> bool:
    if len(bench_slice) < 60:
        return False
    c = bench_slice["Close"]
    sma50 = c.rolling(50).mean()
    return bool(c.iloc[-1] > sma50.iloc[-1] and sma50.iloc[-1] > sma50.iloc[-10])


def forward_label(future: pd.DataFrame, entry: float, atr_pct: float = None) -> tuple[str, float]:
    """triple-barrier 标签 + 前向收益。

    ATR自适应: 若给 atr_pct(入场时ATR占价格%)，障碍按波动率缩放——
    上障碍=2×ATR%，下障碍=1.5×ATR%，让高低波动股用各自合适的目标。
    timeout(到期未触障碍)单列为"震荡",不再强行归为下跌。
    """
    if future.empty:
        return "震荡", 0.0
    hi = future["High"].values
    lo = future["Low"].values
    if atr_pct and atr_pct > 0:
        up_bar = 2.0 * atr_pct
        dn_bar = 1.5 * atr_pct
    else:
        up_bar, dn_bar = TARGET, STOP
    target_px = entry * (1 + up_bar / 100)
    stop_px = entry * (1 - dn_bar / 100)
    label = None
    for i in range(len(future)):
        hit_t = hi[i] >= target_px
        hit_s = lo[i] <= stop_px
        if hit_t and not hit_s:
            label = "上涨"; break
        if hit_s and not hit_t:
            label = "下跌"; break
        if hit_t and hit_s:  # 同日都碰,保守算下跌
            label = "下跌"; break
    end_ret = (future["Close"].iloc[-1] - entry) / entry * 100
    if label is None:
        label = "震荡"  # timeout 未触任何障碍,单列为震荡(不强归涨跌)
    return label, round(float(end_ret), 2)


def run_backtest(tickers, evaluate_fn=evaluate, verbose=True, price_only=False):
    bench = fetch_benchmark()
    if bench is None or bench.empty:
        print("无法获取大盘基准", file=sys.stderr)
        return None
    bench = bench.copy()

    samples = []  # 每条: score, pred_dir, up_prob, label, fwd_ret, market_ok
    for t in tickers:
        sd = fetch(t)
        if not sd.ok:
            if verbose:
                print(f"  跳过 {t}: {sd.error[:50]}", file=sys.stderr)
            continue
        h = sd.history
        n = len(h)
        # 决策点: 从 MIN_HISTORY 到 n-HORIZON，每 STEP 一个
        for idx in range(MIN_HISTORY, n - HORIZON, STEP):
            asof_date = h.index[idx]
            hist_slice = h.iloc[:idx + 1]
            bench_slice = bench[bench.index <= asof_date]
            if len(bench_slice) < 60:
                continue
            f = compute_all_asof(t, hist_slice, bench_slice, sd.fundamentals)
            mkt = market_ok_asof(bench_slice)
            r = evaluate_fn(f, mkt, price_only=price_only)
            pred = predict(r, f, mkt)
            entry = float(hist_slice["Close"].iloc[-1])
            future = h.iloc[idx + 1: idx + 1 + HORIZON]
            label, fwd = forward_label(future, entry, atr_pct=f.get("adr_pct"))
            # 摊平原始因子,供后续训练非线性模型
            ma, hl, vcp, piv, oh, vol = (f["ma"], f["hl52"], f["vcp"], f["pivot"],
                                         f.get("overheat", {}), f["volume"])
            samples.append({
                "ticker": t, "date": str(asof_date.date()),
                "score": r.total, "pred_dir": pred.direction,
                "up_prob": pred.up_prob, "label": label, "fwd_ret": fwd,
                "market_ok": int(mkt), "buyable": int(r.trigger["buyable_now"]),
                # --- 原始因子特征 ---
                "rs": f["rs"], "adr_pct": f.get("adr_pct"),
                "pct_from_high": hl.get("pct_from_high"), "pct_above_low": hl.get("pct_above_low"),
                "ext_ema10": ma["close"] / ma["ema10"] - 1 if ma["ema10"] else None,
                "ext_ema20": ma["close"] / ma["ema20"] - 1 if ma["ema20"] else None,
                "ext_ema50": ma["close"] / ma["ema50"] - 1 if ma["ema50"] else None,
                "vcp_n": vcp["n_contractions"], "vcp_decr": int(vcp["is_decreasing"]),
                "vcp_voldry": int(vcp["vol_dryup"]), "vcp_tight": vcp.get("tightness"),
                "vcp_quality": vcp.get("vcp_quality"),
                "vcp_slope": vcp.get("contraction_slope"),
                "vcp_lfr": vcp.get("last_first_ratio"),
                "vcp_volslope": vcp.get("vol_slope"),
                "vcp_baselen": vcp.get("base_len"),
                "vol_ratio": vol.get("vol_ratio"),
                "pct_over_pivot": piv.get("pct_over_pivot"), "breakout": int(bool(piv.get("breakout"))),
                "pocket_pivot": int(f["pocket_pivot"]),
                "overheat": oh.get("overheat_score"),
                "ret5_atr": oh.get("ret5_atr"), "ret10_atr": oh.get("ret10_atr"),
                "run20": oh.get("run20"), "atr_pctile": oh.get("atr_pctile"),
                "consec_up": oh.get("consec_up"),
            })
        if verbose:
            print(f"  {t}: 累计样本 {len(samples)}", file=sys.stderr)
    return pd.DataFrame(samples)


def report(df: pd.DataFrame, title="回测结果"):
    if df is None or df.empty:
        print("无样本"); return
    print("\n" + "=" * 70)
    print(f"{title}  (样本 {len(df)}, 参数 H={HORIZON} T={TARGET} S={STOP})")
    print("=" * 70)

    # 1) 前向收益 vs 评分的相关性(IC) — 池化Pearson(旧口径,参考)
    ic = df["score"].corr(df["fwd_ret"])
    print(f"池化Pearson IC(参考): {ic:.3f}")

    # 1b) 横截面 Rank IC(Codex建议的正确口径): 每个决策日内按分数排名 vs 收益排名
    def spearman(a, b):
        # numpy 实现,避免依赖 scipy
        ar = pd.Series(a).rank().values
        br = pd.Series(b).rank().values
        if np.std(ar) == 0 or np.std(br) == 0:
            return np.nan
        return float(np.corrcoef(ar, br)[0, 1])
    daily_ic = []
    for d, g in df.groupby("date"):
        if len(g) >= 5:  # 当日至少5只才算横截面
            ric = spearman(g["score"].values, g["fwd_ret"].values)
            if not np.isnan(ric):
                daily_ic.append(ric)
    if daily_ic:
        arr = np.array(daily_ic)
        mean_ic = arr.mean()
        icir = mean_ic / arr.std() if arr.std() > 0 else 0
        pos_rate = (arr > 0).mean()
        print(f"横截面 Rank IC: 均值={mean_ic:+.4f}  ICIR={icir:+.3f}  "
              f"IC>0占比={pos_rate:.1%}  (有效交易日{len(daily_ic)})")
    else:
        print("横截面 Rank IC: 有效交易日不足")

    # 2) 方向命中率
    df["pred_bull"] = df["pred_dir"].isin(["偏多", "弱偏多"])
    df["actual_up"] = df["label"] == "上涨"
    bull = df[df["pred_bull"]]
    if len(bull):
        print(f"看多样本胜率: {bull['actual_up'].mean():.1%}  (n={len(bull)})")
    base_up = df["actual_up"].mean()
    print(f"全样本基础上涨率(基准线): {base_up:.1%}")

    # 3) 分数分档的实际上涨率 + cluster bootstrap 置信区间(按ticker整块重采样)
    print("\n分数分档 → 实际上涨率 [95%CI, 按股票cluster bootstrap]:")
    bins = [(0, 45), (45, 55), (55, 65), (65, 75), (75, 101)]
    rng = np.random.RandomState(42)  # 固定种子,可复现
    for lo, hi in bins:
        seg = df[(df["score"] >= lo) & (df["score"] < hi)]
        if len(seg) < 5:
            continue
        seg_tickers = seg["ticker"].unique()
        # 按ticker分组的上涨标记,便于整块重采样
        by_tk = {tk: seg[seg["ticker"] == tk]["actual_up"].values for tk in seg_tickers}
        boot = []
        for _ in range(500):
            # 有放回抽取同样数量的ticker,合并其所有样本算上涨率
            picked = rng.choice(seg_tickers, size=len(seg_tickers), replace=True)
            vals = np.concatenate([by_tk[tk] for tk in picked])
            if len(vals):
                boot.append(vals.mean())
        b = np.array(boot)
        lo_ci, hi_ci = np.percentile(b, [2.5, 97.5])
        print(f"  [{lo:>2}-{hi:<3}) n={len(seg):>4}  上涨率={seg['actual_up'].mean():.1%}  "
              f"[{lo_ci:.0%},{hi_ci:.0%}]  平均收益={seg['fwd_ret'].mean():+.2f}%  中位={seg['fwd_ret'].median():+.2f}%")

    # 4) buyable 触发样本
    buy = df[df["buyable"] == 1]
    if len(buy):
        print(f"\n触发'可买入': n={len(buy)}  上涨率={buy['actual_up'].mean():.1%}  "
              f"平均收益={buy['fwd_ret'].mean():+.2f}%  中位={buy['fwd_ret'].median():+.2f}%")

    # 5) 标签分布(triple-barrier 三类)
    print(f"\n标签分布: " + "  ".join(f"{k}={v}({v/len(df):.0%})"
          for k, v in df["label"].value_counts().items()))

    return {"ic": ic, "base_up": base_up,
            "rank_ic": float(np.mean(daily_ic)) if daily_ic else None,
            "bull_winrate": float(bull["actual_up"].mean()) if len(bull) else None}


DEFAULT_UNIVERSE = [
    # 大盘科技
    "NVDA", "MU", "AVGO", "AMD", "AAPL", "MSFT", "META", "GOOGL", "AMZN", "TSLA",
    "NFLX", "CRM", "NBIS", "PLTR", "SMCI", "ANET", "PANW", "NOW", "UBER", "SHOP",
    # 半导体/硬件
    "INTC", "QCOM", "TXN", "MRVL", "ON", "MCHP", "LRCX", "KLAC", "AMAT", "ADI",
    "NXPI", "SWKS", "TER", "ENTG", "MPWR", "WDC", "STX", "HPQ", "HPE", "CSCO",
    # 软件/互联网
    "ORCL", "ADBE", "SNOW", "DDOG", "NET", "ZS", "CRWD", "MDB", "TEAM", "WDAY",
    "INTU", "PANW", "FTNT", "OKTA", "TWLO", "DOCU", "ZM", "PINS", "SNAP", "SPOT",
    # 金融
    "JPM", "BAC", "GS", "MS", "WFC", "C", "SCHW", "AXP", "V", "MA",
    "BLK", "SPGI", "CME", "ICE", "PNC", "USB", "TFC", "COF", "BK", "PYPL",
    # 医疗健康
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "CVS", "CI", "HUM", "ISRG", "VRTX", "REGN", "MRNA", "BIIB",
    # 消费
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "TGT", "LOW", "DIS", "KO",
    "PEP", "PG", "CL", "MDLZ", "MO", "PM", "EL", "LULU", "CMG", "YUM",
    # 工业/能源/材料
    "CAT", "BA", "GE", "HON", "UPS", "XOM", "CVX", "COP", "SLB", "DE",
    "LMT", "RTX", "GD", "MMM", "EMR", "ETN", "PH", "ITW", "FDX", "NSC",
    "FCX", "NEM", "NUE", "DOW", "LIN", "APD", "SHW", "ECL", "PPG", "VMC",
    # 中小盘/波动大/含长期弱势(缓解幸存者偏差)
    "F", "GM", "T", "VZ", "PARA", "WBA", "RIVN", "LCID", "AFRM", "ROKU",
    "DKNG", "COIN", "HOOD", "SOFI", "CVNA", "DELL", "RBLX", "U", "DASH", "ABNB",
    "CCL", "NCLH", "AAL", "UAL", "DAL", "MARA", "RIOT", "PLUG", "CHPT", "FSLR",
    "ENPH", "RUN", "BYND", "PTON", "W", "CHWY", "ETSY", "EBAY", "BABA", "PDD",
]
DEFAULT_UNIVERSE = list(dict.fromkeys(DEFAULT_UNIVERSE))  # 去重保序


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=DEFAULT_UNIVERSE)
    ap.add_argument("--save", default="output/backtest_samples.csv")
    ap.add_argument("--price-only", action="store_true", help="纯价格模型,移除基本面(避免未来泄漏)")
    args = ap.parse_args()
    df = run_backtest(args.tickers, price_only=args.price_only)
    if df is not None and not df.empty:
        os.makedirs("output", exist_ok=True)
        df.to_csv(args.save, index=False)
        print(f"\n样本已存: {args.save}", file=sys.stderr)
        report(df)


if __name__ == "__main__":
    main()
