"""PEAD(盈利意外后漂移)验证器 —— Codex 决策者设计的严谨协议。

数据: AlphaVantage EARNINGS(reportedDate真实公布日 + reportTime盘前盘后, 已SEC交叉验证)。
价格: 复用 data.fetch 的日线 + SPY。

Codex 关键防泄漏设计(4点):
1. 禁止季度全样本分位前视: 主信号用【截至入场时可见的历史滚动分位】(该股自己的历史SUE排名)。
2. SUE近零分母修正: 主口径用滚动历史排名+winsorize; 亏损/近零EPS单独报告。
3. 入场留缓冲: 盘后(AMC)→次日开盘; 盘前(BMO)→次日开盘(保守, 除非确认足够早); reportTime不明→排除。
4. 推断: 按日历时间block bootstrap(保同日横截面相关)+公司聚类; 1/5/20/60日样本同折; 减SPY市场中性。

窗口(Codex): 主结论2016-2026; 分段报告检验PEAD衰减; 2022-2026必须同号否则当代no-go。

⚠️幸存者偏差: 当前成分股回溯, 结论仅限"当前大盘股的历史", 不代表当时可投资宇宙。

用法: ./venv/bin/python pead.py --start 2016-01-01
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import datetime as dt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.fetch import fetch, fetch_benchmark
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "cache", "earnings")
PRICE_DIR = os.path.join(os.path.dirname(__file__), "data", "cache", "pead_prices")
HORIZONS = [1, 5, 20, 60]
MIN_HIST_SUE = 8   # 算滚动历史SUE排名所需最少历史财报数

_TIINGO_KEY = os.environ.get("TIINGO_API_KEY", "")


def fetch_long_history(ticker, start="2015-01-01"):
    """PEAD专用: 拉Tiingo长历史(11年)复权日线, 独立缓存, 不碰主fetch的2年缓存。"""
    os.makedirs(PRICE_DIR, exist_ok=True)
    path = os.path.join(PRICE_DIR, f"{ticker}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    url = (f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
           f"?startDate={start}&format=json&token={_TIINGO_KEY}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stock-screener/1.0"})
        arr = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except Exception:
        return pd.DataFrame()
    if not isinstance(arr, list) or not arr:
        return pd.DataFrame()
    rows = [{"Date": pd.Timestamp(x["date"]).tz_localize(None),
             "Open": float(x.get("adjOpen") or x["open"]),
             "High": float(x.get("adjHigh") or x["high"]),
             "Low": float(x.get("adjLow") or x["low"]),
             "Close": float(x.get("adjClose") or x["close"]),
             "Volume": float(x.get("adjVolume") or x["volume"])} for x in arr]
    df = pd.DataFrame(rows).set_index("Date").sort_index()
    df.to_pickle(path)
    return df


def load_earnings():
    """读所有缓存的AV财报, 返回 DataFrame(ticker, fiscal, reported, est, act, reportTime)。"""
    rows = []
    for path in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        t = os.path.basename(path)[:-5]
        d = json.load(open(path))
        for r in d.get("quarterlyEarnings", []):
            rd = r.get("reportedDate")
            est = r.get("estimatedEPS"); act = r.get("reportedEPS")
            if not rd or est in (None, "None", "") or act in (None, "None", ""):
                continue
            try:
                rows.append({"ticker": t, "fiscal": r.get("fiscalDateEnding"),
                             "reported": rd, "est": float(est), "act": float(act),
                             "reportTime": r.get("reportTime", "")})
            except ValueError:
                continue
    df = pd.DataFrame(rows).sort_values(["ticker", "reported"]).reset_index(drop=True)
    return df


def compute_sue(df):
    """SUE原始 = act-est。主信号 = 该股【截至当前】历史SUE的滚动分位排名(防前视)。
    另算 winsorize 的 (act-est)/|est| 作稳健性。"""
    df = df.copy()
    df["sue_raw"] = df["act"] - df["est"]
    # 滚动历史分位: 对每只票, 用其此前所有财报的sue_raw给当前排名(0~1), 不含未来
    df["sue_rank"] = np.nan
    for t, g in df.groupby("ticker"):
        vals = g["sue_raw"].values
        ranks = np.full(len(vals), np.nan)
        for i in range(len(vals)):
            if i >= MIN_HIST_SUE:
                hist = vals[:i]  # 严格只用过去
                ranks[i] = (hist < vals[i]).mean()
        df.loc[g.index, "sue_rank"] = ranks
    return df


def entry_date(reported, report_time, trading_days):
    """Codex入场规则: AMC→次日开盘; BMO→次日开盘(保守); 不明→次日。
    统一保守取【公布日之后的下一个交易日】开盘。返回该交易日的Timestamp或None。
    ⚠️必须紧邻reported(<=5交易日): 否则说明价格数据不覆盖该财报期, 丢弃(防错配到序列首日)。"""
    try:
        rd = pd.Timestamp(reported)
    except Exception:
        return None
    future = trading_days[trading_days > rd]
    if len(future) == 0:
        return None
    nxt = future[0]
    # 防错配: 入场日必须在公布日后5个自然交易日内, 否则价格数据没覆盖该期
    if (nxt - rd).days > 7:
        return None
    return nxt


def forward_car(hist, spy, entry_ts, horizons):
    """从 entry_ts 开盘入场, 各horizon的市场中性CAR = 个股收益 - SPY同期收益。"""
    if entry_ts not in hist.index:
        return {}
    i = hist.index.get_loc(entry_ts)
    entry_px = float(hist["Open"].iloc[i])
    # SPY 对齐
    if entry_ts not in spy.index:
        return {}
    si = spy.index.get_loc(entry_ts)
    spy_entry = float(spy["Open"].iloc[si])
    out = {}
    for H in horizons:
        if i + H < len(hist) and si + H < len(spy):
            stock_ret = (float(hist["Close"].iloc[i + H]) - entry_px) / entry_px
            spy_ret = (float(spy["Close"].iloc[si + H]) - spy_entry) / spy_entry
            out[f"car{H}"] = (stock_ret - spy_ret) * 100
    return out


def build_events(edf, start, end):
    """对每个财报事件, 算入场日 + 各horizon市场中性CAR。用长历史价格(11年)。"""
    spy = fetch_long_history("SPY")
    if spy.empty:
        spy = fetch_benchmark()
    events = []
    for t, g in edf.groupby("ticker"):
        h = fetch_long_history(t)
        if h.empty:
            continue
        tdays = h.index
        for _, r in g.iterrows():
            if not (start <= r["reported"] <= end):
                continue
            if pd.isna(r["sue_rank"]):
                continue
            ets = entry_date(r["reported"], r["reportTime"], tdays)
            if ets is None:
                continue
            cars = forward_car(h, spy, ets, HORIZONS)
            if "car20" not in cars:
                continue
            events.append({"ticker": t, "reported": r["reported"],
                           "entry": str(ets.date()), "sue_rank": r["sue_rank"],
                           "sue_raw": r["sue_raw"], "est": r["est"], "act": r["act"],
                           **cars})
    return pd.DataFrame(events)


def block_bootstrap_topbottom(ev, H, n_boot=2000, seed=42):
    """按日历月成块bootstrap top-bottom分位CAR差, 保同月横截面相关。"""
    col = f"car{H}"
    ev = ev.dropna(subset=[col, "sue_rank"]).copy()
    if len(ev) < 30:
        return None, None, None
    ev["top"] = ev["sue_rank"] >= 0.8
    ev["bot"] = ev["sue_rank"] <= 0.2
    ev["month"] = pd.to_datetime(ev["reported"]).dt.to_period("M").astype(str)
    months = ev["month"].unique()
    month_groups = {m: ev[ev["month"] == m] for m in months}
    rng = np.random.RandomState(seed)
    diffs = []
    for _ in range(n_boot):
        pick = rng.choice(months, size=len(months), replace=True)
        sample = pd.concat([month_groups[m] for m in pick])
        top = sample[sample["top"]][col]
        bot = sample[sample["bot"]][col]
        if len(top) >= 3 and len(bot) >= 3:
            diffs.append(top.mean() - bot.mean())
    if not diffs:
        return None, None, None
    point = np.mean([month_groups[m][month_groups[m]["top"]][col].mean() -
                     month_groups[m][month_groups[m]["bot"]][col].mean()
                     for m in months if month_groups[m]["top"].any() and month_groups[m]["bot"].any()])
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(point), float(lo), float(hi)


def report_segment(ev, name):
    """报一个时间段的分位单调性 + top-bottom + CI。"""
    print(f"\n【{name}】 事件{len(ev)}  股票{ev['ticker'].nunique()}")
    if len(ev) < 30:
        print("  样本不足(<30), 跳过"); return None
    # SUE分位五组 → 各horizon平均市场中性CAR
    ev = ev.copy()
    ev["q"] = pd.cut(ev["sue_rank"], [0, 0.2, 0.4, 0.6, 0.8, 1.01],
                     labels=["Q1低", "Q2", "Q3", "Q4", "Q5高"])
    print("  SUE分位 → 20日市场中性CAR均值:")
    mono = []
    for q, gg in ev.groupby("q", observed=True):
        if len(gg):
            m = gg["car20"].mean()
            mono.append(m)
            print(f"    {q}: n={len(gg):>4}  CAR20={m:+.2f}%  CAR5={gg['car5'].mean():+.2f}%")
    for H in HORIZONS:
        point, lo, hi = block_bootstrap_topbottom(ev, H)
        if point is not None:
            sig = "✓" if lo > 0 else "✗"
            print(f"  top-bottom CAR{H}: {point:+.2f}%  95%CI[{lo:+.2f},{hi:+.2f}] {sig}")
    return mono


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--save", default="output/pead_events.csv")
    args = ap.parse_args()

    print("=" * 70)
    print("PEAD 验证 (Codex协议: 滚动历史SUE分位/次日开盘入场/市场中性CAR/月度块bootstrap)")
    print("=" * 70)
    edf = load_earnings()
    edf = compute_sue(edf)
    print(f"财报事件(含SUE): {len(edf)}  股票: {edf['ticker'].nunique()}  "
          f"reportTime齐: {(edf['reportTime']!='').mean():.0%}")
    print("⚠️ 幸存者偏差: 结论仅限'当前大盘股的历史', 非当时可投资宇宙")

    ev = build_events(edf, args.start, args.end)
    if ev.empty:
        print("无有效事件"); return
    os.makedirs("output", exist_ok=True)
    ev.to_csv(args.save, index=False)

    # 主窗口 + 分段(检验PEAD衰减)
    report_segment(ev[ev["reported"] < "2026-12-31"], "主窗口 2016-2026")
    segs = [("2016-2021", "2016-01-01", "2021-12-31"),
            ("2022-2026 (当代, 必须同号)", "2022-01-01", "2026-12-31")]
    for nm, s, e in segs:
        report_segment(ev[(ev["reported"] >= s) & (ev["reported"] <= e)], nm)

    print(f"\n事件明细存 {args.save}")
    print("注: 这是25只pilot gate。正式go/no-go需攒满138只(每天抓25, 6天)。")


if __name__ == "__main__":
    main()
