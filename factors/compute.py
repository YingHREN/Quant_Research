"""因子计算：把日线数据转成 KovaView/CAN SLIM+VCP 框架里的各项技术因子。

所有函数输入 StockData / DataFrame，输出标量或结构化 dict。
不做任何交易决策，只算"事实"。决策在 scoring 层。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def moving_averages(hist: pd.DataFrame) -> dict:
    c = hist["Close"]
    return {
        "close": float(c.iloc[-1]),
        "ema10": float(_ema(c, 10).iloc[-1]),
        "ema20": float(_ema(c, 20).iloc[-1]),
        "ema50": float(_ema(c, 50).iloc[-1]),
        "sma50": float(_sma(c, 50).iloc[-1]) if len(c) >= 50 else None,
        "sma200": float(_sma(c, 200).iloc[-1]) if len(c) >= 200 else None,
    }


def rs_rating(hist: pd.DataFrame, bench: pd.DataFrame) -> float | None:
    """相对强度评分 0~100。

    经典 IBD RS 需全市场排名，这里用个股 vs 大盘的多周期加权超额收益近似。
    自适应数据长度：数据足够时用 63/126/189/252 日，不足(如免费源仅~100天)时
    退化为 21/42/63 日，仍能反映近中期相对强弱(标注为近似)。
    """
    if bench is None or bench.empty:
        return None
    c = hist["Close"]
    b = bench["Close"]
    df = pd.concat([c, b], axis=1, keys=["s", "b"]).dropna()
    n = len(df)
    if n < 40:
        return None
    if n >= 260:
        periods = [(63, 2.0), (126, 1.0), (189, 1.0), (252, 1.0)]
    else:  # 短窗口降级
        periods = [(21, 2.0), (42, 1.0), (min(63, n - 1), 1.0)]
    score = 0.0
    wsum = 0.0
    for p, w in periods:
        if n > p:
            sr = df["s"].iloc[-1] / df["s"].iloc[-p] - 1
            br = df["b"].iloc[-1] / df["b"].iloc[-p] - 1
            score += w * (sr - br)
            wsum += w
    if wsum == 0:
        return None
    excess = score / wsum
    val = 100 / (1 + np.exp(-6 * excess))
    return round(float(val), 1)


def high_low_52w(hist: pd.DataFrame) -> dict:
    """52周高低。数据不足252天时用可得最长窗口，并标注 approx。"""
    c = hist["Close"]
    full = len(c) >= 252
    window = c.iloc[-252:] if full else c
    hi = float(window.max())
    lo = float(window.min())
    price = float(c.iloc[-1])
    return {
        "high_52w": hi,
        "low_52w": lo,
        "pct_from_high": (hi - price) / hi * 100 if hi else None,
        "pct_above_low": (price - lo) / lo * 100 if lo else None,
        "approx": not full,   # True 表示窗口不足一年，是近似值
        "window_days": len(window),
    }


def adr_pct(hist: pd.DataFrame, n: int = 20) -> float | None:
    """平均日振幅% = mean((High-Low)/Close) over n days。波动率口径，非成交额。"""
    if len(hist) < n:
        return None
    rng = (hist["High"] - hist["Low"]) / hist["Close"]
    return round(float(rng.iloc[-n:].mean() * 100), 2)


def avg_dollar_volume(hist: pd.DataFrame, n: int = 20) -> float | None:
    """平均日成交额(美元) = mean(Close*Volume)。流动性口径，与 ADR 分开。"""
    if len(hist) < n:
        return None
    dv = hist["Close"] * hist["Volume"]
    return float(dv.iloc[-n:].mean())


def volume_stats(hist: pd.DataFrame) -> dict:
    v = hist["Volume"]
    avg50 = float(v.iloc[-50:].mean()) if len(v) >= 50 else float(v.mean())
    today = float(v.iloc[-1])
    return {
        "vol_today": today,
        "vol_avg50": avg50,
        "vol_ratio": today / avg50 if avg50 else None,  # 今日量/50日均量
    }


def pocket_pivot(hist: pd.DataFrame, lookback: int = 10) -> bool:
    """当日上涨，且成交量 > 过去 lookback 日内所有下跌日的最大成交量。"""
    if len(hist) < lookback + 1:
        return False
    c = hist["Close"]
    v = hist["Volume"]
    up_today = c.iloc[-1] > c.iloc[-2]
    if not up_today:
        return False
    window = hist.iloc[-(lookback + 1):-1]
    down_days = window[window["Close"] < window["Close"].shift(1)]
    if down_days.empty:
        return v.iloc[-1] > 0
    return v.iloc[-1] > down_days["Volume"].max()


def _zigzag_pivots(prices: np.ndarray, pct: float = 5.0):
    """ZigZag 峰谷检测：只保留累计反转 >= pct% 的显著转折点。

    返回 [(idx, price, kind)], kind 为 'H'(峰) 或 'L'(谷)，交替出现。
    这是识别"有意义的回调腿"的标准方法——过滤掉日常噪声波动，
    只留下真正的 swing high/low，从而能正确数出 VCP 的收缩腿。
    """
    n = len(prices)
    if n < 5:
        return []
    thr = pct / 100.0
    pivots = []
    last_pivot_idx = 0
    last_pivot_px = prices[0]
    trend = 0  # +1 上行(找更高高点), -1 下行(找更低低点), 0 未定
    ext_idx, ext_px = 0, prices[0]  # 当前段的极值候选

    for i in range(1, n):
        px = prices[i]
        if trend >= 0:
            if px > ext_px:
                ext_idx, ext_px = i, px
            # 从最近高点回撤超过阈值 → 确认一个峰
            if ext_px > 0 and (ext_px - px) / ext_px >= thr:
                pivots.append((ext_idx, ext_px, "H"))
                last_pivot_idx, last_pivot_px = ext_idx, ext_px
                trend = -1
                ext_idx, ext_px = i, px
        if trend <= 0:
            if px < ext_px:
                ext_idx, ext_px = i, px
            # 从最近低点反弹超过阈值 → 确认一个谷
            if ext_px > 0 and (px - ext_px) / ext_px >= thr:
                pivots.append((ext_idx, ext_px, "L"))
                last_pivot_idx, last_pivot_px = ext_idx, ext_px
                trend = 1
                ext_idx, ext_px = i, px
    return pivots


def vcp_analysis(hist: pd.DataFrame, max_lookback: int = 250) -> dict:
    """VCP(Volatility Contraction Pattern)【形态候选】检测 —— Codex修正版(precision-first)。

    关键: 先框 base 通过硬门控, 才在 base 内跑 ZigZag 找收缩腿。宁缺毋滥, 允许漏报。
    vcp_quality 仅作形态描述, 【不进预测评分/买入排序】(VCP买点已回测证伪)。

    硬门控(任一不满足 → 非VCP, 直接返回empty):
    1. 框base: 20-80日窗口, base深度<=35%, 距52周高>=75%, 价>MA50, MA50>=MA200(或MA50不降)
    2. 排除单边拉升(治AAPL误判): base涨幅>15%且效率比>0.5 → 拒; 近20日涨幅>12% → 拒
    3. base内至少2条收缩腿, 且严格递减: 每腿 <= 前腿×0.95, 末腿/首腿<=0.75, 首腿-末腿>=3点
    现价超pivot>5% → 标 is_extended(已延伸), 不算干净突破买点。
    """
    empty = {"contractions": [], "n_contractions": 0, "is_decreasing": False,
             "vol_dryup": False, "vola_contract": False, "tightness": None,
             "vcp_pivot": None, "leg_vols_decreasing": False, "base_len": 0,
             "contraction_slope": None, "last_first_ratio": None, "vol_slope": None,
             "vcp_quality": 0.0, "adaptive_pct": None, "is_extended": False,
             "reject_reason": None}
    if len(hist) < 60:
        return {**empty, "reject_reason": "历史不足"}

    c_all = hist["Close"].values
    price = float(c_all[-1])
    # 趋势门控: MA50/MA200, 52周高
    ma50 = float(np.mean(c_all[-50:])) if len(c_all) >= 50 else None
    ma200 = float(np.mean(c_all[-200:])) if len(c_all) >= 200 else None
    hi_52w = float(np.max(c_all[-252:])) if len(c_all) >= 252 else float(np.max(c_all))
    if ma50 is None or price <= ma50:
        return {**empty, "reject_reason": "价格未站上MA50"}
    if ma200 is not None and ma50 < ma200:
        return {**empty, "reject_reason": "MA50<MA200(非上升趋势)"}
    if price / hi_52w < 0.75:
        return {**empty, "reject_reason": "距52周高>25%"}
    # 近20日涨幅过滤(追高/单边拉升)
    if len(c_all) > 20 and (price / c_all[-21] - 1) > 0.12:
        return {**empty, "reject_reason": "近20日涨幅>12%(加速上涨非整理)"}

    # 框 base: 从长(80)到短(20)找第一个满足门控的窗口
    seg_all = hist.iloc[-max_lookback:]
    chosen = None
    for base_days in range(80, 19, -5):
        if base_days > len(seg_all) - 1:
            continue
        b = seg_all.iloc[-base_days:]
        bh, bl = float(b["High"].max()), float(b["Low"].min())
        base_depth = (bh - bl) / bh if bh else 1.0
        if base_depth > 0.35:
            continue
        bc = b["Close"].values
        base_return = bc[-1] / bc[0] - 1
        # 效率比: 净位移/总行程, 高=单边趋势, 低=横盘震荡
        total_travel = np.sum(np.abs(np.diff(bc)))
        eff_ratio = abs(bc[-1] - bc[0]) / total_travel if total_travel > 0 else 1.0
        if base_return > 0.15 and eff_ratio > 0.50:
            continue  # 单边拉升, 拒
        chosen = b
        break
    if chosen is None:
        return {**empty, "reject_reason": "无合格base(深度/单边/长度不满足)"}

    seg = chosen
    close = seg["Close"].values
    high = seg["High"].values
    low = seg["Low"].values
    vol = seg["Volume"].values

    # base内波动率自适应 ZigZag 阈值, 限3%-10%
    atr = _atr(seg, min(20, len(seg) - 1)) or (close[-1] * 0.03)
    adaptive_pct = min(max(atr / close[-1] * 100 * 1.5, 3.0), 10.0)
    pivots = _zigzag_pivots(close, pct=adaptive_pct)
    if len(pivots) < 3:
        return {**empty, "reject_reason": "base内峰谷不足", "adaptive_pct": round(adaptive_pct, 2)}

    # 提取收缩腿: 相邻 (H, L) 对
    legs = []
    for j in range(len(pivots) - 1):
        a, bb = pivots[j], pivots[j + 1]
        if a[2] == "H" and bb[2] == "L":
            depth = (a[1] - bb[1]) / a[1] * 100
            if depth > 2:
                legs.append((a[0], bb[0], round(float(depth), 1)))
    if len(legs) < 2:
        return {**empty, "reject_reason": "base内收缩腿<2", "adaptive_pct": round(adaptive_pct, 2)}

    legs = legs[-4:]
    contractions = [d for _, _, d in legs]
    n = len(contractions)

    # 严格递减(Codex): 每腿<=前腿×0.95, 末腿/首腿<=0.75, 首腿-末腿>=3点。无绝对容差。
    strictly_decr = all(contractions[i + 1] <= contractions[i] * 0.95 for i in range(n - 1))
    lfr = contractions[-1] / contractions[0] if contractions[0] > 0 else 1.0
    span_ok = (contractions[0] - contractions[-1]) >= 3.0
    is_decreasing = strictly_decr and lfr <= 0.75 and span_ok
    if not is_decreasing:
        return {**empty, "reject_reason": f"收缩腿未严格递减 {contractions}",
                "contractions": contractions, "n_contractions": n,
                "adaptive_pct": round(adaptive_pct, 2)}

    # 量能
    leg_vols = [float(vol[pk:tr + 1].mean()) for pk, tr, _ in legs if tr > pk]
    leg_vols_decreasing = len(leg_vols) >= 2 and all(
        leg_vols[i] >= leg_vols[i + 1] * 0.95 for i in range(len(leg_vols) - 1))
    vol_dryup = len(vol) >= 50 and vol[-10:].mean() < vol[-50:].mean()
    rng = (high - low) / close
    vola_contract = len(rng) >= 40 and rng[-10:].mean() < rng[-40:-10].mean()
    tail = close[-10:]
    tightness = float(np.std(tail) / np.mean(tail) * 100) if len(tail) >= 5 else None

    last_highs = [p for p in pivots if p[2] == "H"]
    vcp_pivot = float(last_highs[-1][1]) if last_highs else None
    # 现价超pivot>5% → 已延伸
    is_extended = bool(vcp_pivot and price > vcp_pivot * 1.05)
    base_len = int(len(close) - legs[0][0])

    contraction_slope = round(float(np.polyfit(np.arange(n), contractions, 1)[0]), 3)
    last_first_ratio = round(lfr, 3)
    vol_slope = None
    if len(leg_vols) >= 2:
        sl = np.polyfit(np.arange(len(leg_vols)), leg_vols, 1)[0]
        vol_slope = round(float(sl / (np.mean(leg_vols) + 1e-9)), 4)

    # 质量分(仅形态描述, 不进预测评分): 通过硬门控后按收敛程度给0.5~1.0
    q = 0.5
    if last_first_ratio < 0.6: q += 0.2
    if vol_dryup: q += 0.15
    if tightness is not None and tightness < 5: q += 0.15
    vcp_quality = round(min(q, 1.0), 3)

    return {
        "contractions": contractions, "n_contractions": n, "is_decreasing": is_decreasing,
        "vol_dryup": vol_dryup, "vola_contract": vola_contract,
        "tightness": round(tightness, 2) if tightness is not None else None,
        "vcp_pivot": round(vcp_pivot, 2) if vcp_pivot else None,
        "leg_vols_decreasing": leg_vols_decreasing, "base_len": base_len,
        "contraction_slope": contraction_slope, "last_first_ratio": last_first_ratio,
        "vol_slope": vol_slope, "vcp_quality": vcp_quality,
        "adaptive_pct": round(adaptive_pct, 2), "is_extended": is_extended,
        "reject_reason": None,
    }



def pivot_breakout(hist: pd.DataFrame, base_lookback: int = 20) -> dict:
    """Pivot 突破判定：pivot = 近期整理区最高点；今日收盘是否突破 + 是否放量。"""
    c = hist["Close"]
    v = hist["Volume"]
    if len(c) < base_lookback + 2:
        return {"pivot": None, "breakout": False, "vol_confirm": False, "pct_over_pivot": None}
    base = c.iloc[-(base_lookback + 1):-1]  # 不含今日
    pivot = float(base.max())
    price = float(c.iloc[-1])
    avg50 = v.iloc[-50:].mean() if len(v) >= 50 else v.mean()
    vol_ratio = v.iloc[-1] / avg50 if avg50 else 0
    breakout = price > pivot
    return {
        "pivot": pivot,
        "breakout": breakout,
        "vol_confirm": bool(vol_ratio >= 1.4),
        "vol_ratio": round(float(vol_ratio), 2),
        "pct_over_pivot": round((price - pivot) / pivot * 100, 2) if pivot else None,
    }


def _atr(hist: pd.DataFrame, n: int = 20) -> float | None:
    if len(hist) < n + 1:
        return None
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return float(tr.iloc[-n:].mean())


def tight_platform(hist: pd.DataFrame) -> dict:
    """高位紧凑平台(Codex双轨方案的第二类, 独立于VCP_STRICT)。

    治BMO这类"低波动股窄幅整理": 形态像VCP但两腿都太浅, 被VCP严格阈值剔除。
    这类不叫VCP(避免泛化误判AAPL), 单独标 TIGHT_PLATFORM, 只报"平台接近突破"。
    防AAPL误判仍靠"排除单边加速上涨"硬门控(近20日涨幅), 对低波动股不豁免。
    """
    empty = {"is_platform": False, "reason": None, "range_pct": None,
             "vol_dryup_pct": None, "platform_pivot": None}
    c = hist["Close"].values
    if len(c) < 60:
        return {**empty, "reason": "历史不足"}
    px = float(c[-1])
    ma50 = float(np.mean(c[-50:]))
    ma200 = float(np.mean(c[-200:])) if len(c) >= 200 else None
    hi52 = float(np.max(c[-252:])) if len(c) >= 252 else float(np.max(c))
    atr_pct = (_atr(hist, 20) or px * 0.02) / px * 100

    # 趋势门控(与VCP一致): 价>MA50>MA200
    if px <= ma50:
        return {**empty, "reason": "价未站上MA50"}
    if ma200 is not None and ma50 < ma200:
        return {**empty, "reason": "MA50<MA200"}
    # 距52周高<=10%(高位)
    if px / hi52 < 0.90:
        return {**empty, "reason": "距52周高>10%"}
    # 排除单边加速上涨(防AAPL, 不因低波动豁免)
    if len(c) > 20 and (px / c[-21] - 1) > 0.12:
        return {**empty, "reason": "近20日涨幅>12%(加速上涨)"}

    # 近15-25日平台: 区间宽度 <= max(6%, 4×ATR%)
    win = c[-20:]
    rng_pct = (win.max() - win.min()) / win.max() * 100
    width_cap = max(6.0, 4 * atr_pct)
    if rng_pct > width_cap:
        return {**empty, "reason": f"区间宽度{rng_pct:.1f}%>{width_cap:.1f}%(非紧凑)"}
    # base净涨幅<=8% 且 效率比<=0.35(横盘而非趋势)
    base_ret = win[-1] / win[0] - 1
    travel = np.sum(np.abs(np.diff(win)))
    eff = abs(win[-1] - win[0]) / travel if travel > 0 else 1.0
    if base_ret > 0.08 or eff > 0.35:
        return {**empty, "reason": "非横盘(净涨幅或效率比过高)"}

    # 量能: 近10日中位量 vs 前30-10日, 下降>=20% 更好(不强制)
    v = hist["Volume"].values
    vol_dryup_pct = None
    if len(v) >= 40:
        recent = np.median(v[-10:]); prior = np.median(v[-40:-10])
        vol_dryup_pct = (1 - recent / prior) * 100 if prior else None

    platform_pivot = float(win.max())
    return {"is_platform": True, "reason": None,
            "range_pct": round(rng_pct, 1),
            "vol_dryup_pct": round(vol_dryup_pct, 0) if vol_dryup_pct is not None else None,
            "platform_pivot": round(platform_pivot, 2)}


def overheat(hist: pd.DataFrame) -> dict:
    """过热/动量衰竭因子(Codex 建议的核心新增)。

    捕捉"太强=透支"的状态,让评分能表达非单调关系。数值越大越过热。
    """
    c = hist["Close"]
    price = float(c.iloc[-1])
    atr = _atr(hist, 20) or (price * 0.02)
    out = {}

    # 短期涨幅按 ATR 标准化(涨太快=过热)
    if len(c) > 10:
        out["ret5_atr"] = round(float((price - c.iloc[-6]) / atr), 2)
        out["ret10_atr"] = round(float((price - c.iloc[-11]) / atr), 2)
    else:
        out["ret5_atr"] = out["ret10_atr"] = None

    # 距均线偏离(离 MA20/MA50 越远越过热)
    ma20 = float(c.iloc[-20:].mean()) if len(c) >= 20 else price
    ma50 = float(c.iloc[-50:].mean()) if len(c) >= 50 else price
    out["ext_ma20"] = round((price / ma20 - 1) * 100, 2)
    out["ext_ma50"] = round((price / ma50 - 1) * 100, 2)

    # 连续上涨天数
    ups = 0
    for i in range(len(c) - 1, 0, -1):
        if c.iloc[i] > c.iloc[i - 1]:
            ups += 1
        else:
            break
    out["consec_up"] = ups

    # 过去20日最大涨幅(从20日低点到现在)
    if len(c) >= 20:
        lo20 = float(c.iloc[-20:].min())
        out["run20"] = round((price - lo20) / lo20 * 100, 2)
    else:
        out["run20"] = None

    # ATR 相对一年分位(波动率是否异常放大)
    if len(hist) >= 60:
        rng = (hist["High"] - hist["Low"]) / hist["Close"]
        cur = float(rng.iloc[-5:].mean())
        hist_rng = rng.iloc[-252:] if len(rng) >= 252 else rng
        pct = float((hist_rng < cur).mean())
        out["atr_pctile"] = round(pct, 2)
    else:
        out["atr_pctile"] = None

    # 综合过热分 0~100(越高越过热,用于非单调惩罚)
    score = 0.0
    if out["ext_ma20"] is not None:
        score += min(max(out["ext_ma20"], 0) * 2, 30)   # 离MA20每1%给2分,封顶30
    if out["run20"] is not None:
        score += min(max(out["run20"], 0) * 0.8, 30)     # 20日涨幅
    if out["ret10_atr"] is not None:
        score += min(max(out["ret10_atr"], 0) * 4, 25)   # ATR标准化涨速
    if out["atr_pctile"] is not None:
        score += out["atr_pctile"] * 15                  # 波动率分位
    out["overheat_score"] = round(min(score, 100), 1)
    return out


def compute_all(sd, bench: pd.DataFrame) -> dict:
    """汇总所有因子为一个 dict，供 scoring 层消费。"""
    h = sd.history
    return {
        "ticker": sd.ticker,
        "ma": moving_averages(h),
        "rs": rs_rating(h, bench),
        "hl52": high_low_52w(h),
        "adr_pct": adr_pct(h),
        "avg_dollar_vol": avg_dollar_volume(h),
        "volume": volume_stats(h),
        "pocket_pivot": pocket_pivot(h),
        "vcp": vcp_analysis(h),
        "pivot": pivot_breakout(h),
        "overheat": overheat(h),
        "fundamentals": sd.fundamentals,
    }


def compute_all_asof(ticker: str, hist: pd.DataFrame, bench: pd.DataFrame,
                     fundamentals: dict) -> dict:
    """回测用：在给定(已切到某历史日期为止的)日线上算全部因子。

    hist/bench 都应已按同一 as-of 日期切片。fundamentals 无历史版本时
    沿用当前值(近似，回测里作为常量处理，主要检验技术+RS因子的预测力)。
    """
    return {
        "ticker": ticker,
        "ma": moving_averages(hist),
        "rs": rs_rating(hist, bench),
        "hl52": high_low_52w(hist),
        "adr_pct": adr_pct(hist),
        "avg_dollar_vol": avg_dollar_volume(hist),
        "volume": volume_stats(hist),
        "pocket_pivot": pocket_pivot(hist),
        "vcp": vcp_analysis(hist),
        "pivot": pivot_breakout(hist),
        "overheat": overheat(hist),
        "fundamentals": fundamentals,
    }
