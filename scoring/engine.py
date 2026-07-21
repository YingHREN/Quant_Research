"""评分引擎：实现 Codex 决策的三层框架。

过滤层(硬门槛) -> 打分层(100分,7维加权) -> 分档(A/B/C/淘汰) + 触发层判定。

注意：基本面数据(EPS/营收)来自 yfinance 近似，常有缺失。策略是——
缺失时不直接淘汰(否则免费源下几乎全被刷掉)，而是记为 "unknown"，
在打分时给中性分，并在结果里标注 data_gaps，让用户知道哪些没验证。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------- 过滤层 ----------

def hard_filters(f: dict) -> dict:
    """返回每条硬门槛的通过情况。基本面缺失记 None(不算失败)。"""
    ma = f["ma"]
    hl = f["hl52"]
    fund = f["fundamentals"]
    checks = {}

    checks["price_gt_10"] = ma["close"] > 10
    checks["price_above_ema50"] = ma["close"] > ma["ema50"]
    checks["ema10_gt_ema20"] = ma["ema10"] > ma["ema20"]
    # 52周高低：窗口不足一年(免费源仅~100天)时降级为 None，不当淘汰项
    approx = hl.get("approx", False)
    if approx:
        checks["above_52w_low_70"] = None
        checks["near_52w_high_20"] = None
    else:
        checks["above_52w_low_70"] = (hl["pct_above_low"] is not None and hl["pct_above_low"] > 70)
        checks["near_52w_high_20"] = (hl["pct_from_high"] is not None and hl["pct_from_high"] < 20)
    checks["rs_gt_90"] = (f["rs"] is not None and f["rs"] > 90)
    checks["liquidity"] = (f["avg_dollar_vol"] is not None and f["avg_dollar_vol"] > 5_000_000)

    # 基本面：有数据才判，缺失记 None
    checks["eps_yoy_gt_25"] = (fund.get("eps_yoy") > 25) if fund.get("eps_yoy") is not None else None
    checks["rev_yoy_gt_20"] = (fund.get("rev_yoy") > 20) if fund.get("rev_yoy") is not None else None

    return checks


def passes_hard_filter(checks: dict) -> bool:
    """技术+流动性硬门槛必须全过；数据缺失(None)的项放行(不淘汰)。"""
    must_keys = ["price_gt_10", "price_above_ema50", "ema10_gt_ema20",
                 "rs_gt_90", "liquidity"]
    for k in must_keys:
        if not checks.get(k):
            return False
    # 以下项数据不足时为 None，放行；有明确 False 才淘汰
    for k in ["above_52w_low_70", "near_52w_high_20", "eps_yoy_gt_25", "rev_yoy_gt_20"]:
        if checks.get(k) is False:
            return False
    return True


# ---------- 打分层 (100分) ----------

def score_vcp(f: dict) -> float:
    """VCP 结构质量，满分 20。"""
    v = f["vcp"]
    s = 0.0
    if v["n_contractions"] >= 3:
        s += 4
    elif v["n_contractions"] == 2:
        s += 2
    if v["is_decreasing"]:
        s += 5
    if v["vol_dryup"]:
        s += 4
    if v["vola_contract"]:
        s += 4
    if v["tightness"] is not None and v["tightness"] < 5:  # 末端收盘很紧
        s += 3
    return min(s, 20)


def score_fundamentals(f: dict) -> tuple[float, bool]:
    """基本面质量，满分 20。返回(分数, 是否有数据)。缺失给中性 10 分。"""
    fund = f["fundamentals"]
    eps, rev = fund.get("eps_yoy"), fund.get("rev_yoy")
    if eps is None and rev is None:
        return 10.0, False  # 中性
    s = 0.0
    if eps is not None:
        if eps > 100:
            s += 7
        elif eps > 50:
            s += 6
        elif eps > 25:
            s += 4
        elif eps > 0:
            s += 2
    if fund.get("eps_accel"):
        s += 5
    if rev is not None:
        if rev > 50:
            s += 6
        elif rev > 30:
            s += 5
        elif rev > 20:
            s += 3
        elif rev > 0:
            s += 1
    if eps is not None and eps > 0 and rev is not None and rev > 0:
        s += 2  # 增长质量：利润营收同步为正
    return min(s, 20), True


def score_market(f: dict, market_ok: bool, industry_rs: float | None = None) -> float:
    """大盘与行业环境，满分 20。行业数据免费源难拿，缺失时按大盘给。"""
    s = 0.0
    if market_ok:
        s += 8
    # 行业强度缺省用个股RS近似(退化处理)
    proxy = industry_rs if industry_rs is not None else f["rs"]
    if proxy is not None:
        if proxy >= 95:
            s += 9
        elif proxy >= 85:
            s += 6
        elif proxy >= 70:
            s += 3
    if market_ok:
        s += 3  # 突破环境健康的粗略加分
    return min(s, 20)


def score_rs_trend(f: dict) -> float:
    """相对强度与趋势，满分 15。"""
    s = 0.0
    rs = f["rs"]
    if rs is not None:
        if rs >= 98:
            s += 8
        elif rs >= 95:
            s += 6
        elif rs >= 90:
            s += 4
        elif rs >= 80:
            s += 2
    ma = f["ma"]
    if ma["ema10"] > ma["ema20"] and ma["sma50"] and ma["ema20"] > ma["sma50"]:
        s += 4  # 均线多头排列
    hl = f["hl52"]
    if hl["pct_from_high"] is not None and hl["pct_from_high"] < 10:
        s += 3
    return min(s, 15)


def score_breakout(f: dict) -> float:
    """突破量价质量，满分 15。"""
    s = 0.0
    p = f["pivot"]
    vr = p.get("vol_ratio") or 0
    if vr >= 1.8:
        s += 7
    elif vr >= 1.4:
        s += 5
    elif vr >= 1.0:
        s += 2
    if p.get("breakout"):
        s += 3
    if p.get("pct_over_pivot") is not None and 0 <= p["pct_over_pivot"] <= 5:
        s += 3  # 在理想买区
    if f["pocket_pivot"]:
        s += 2
    return min(s, 15)


def score_institution(f: dict) -> float:
    """机构行为，满分 5。免费源无13F，用量能承接近似(退化)。"""
    vol = f["volume"]
    if vol.get("vol_ratio") and vol["vol_ratio"] > 1.2:
        return 3.0
    return 2.0  # 中性


def score_risk_reward(f: dict) -> float:
    """风险收益结构，满分 5。上方空间(距高点) vs 止损距离粗算。"""
    hl = f["hl52"]
    adr = f["adr_pct"]
    s = 2.5
    if hl["pct_from_high"] is not None and hl["pct_from_high"] > 5:
        s += 1.5  # 有上行空间
    if adr is not None and adr < 6:
        s += 1.0  # 波动不过大，止损可控
    return min(s, 5)


@dataclass
class ScoreResult:
    ticker: str
    total: float
    grade: str
    breakdown: dict
    passed_filter: bool
    filter_checks: dict
    trigger: dict
    data_gaps: list = field(default_factory=list)


def grade_of(total: float) -> str:
    if total >= 85:
        return "A (可执行)"
    if total >= 75:
        return "B (重点观察)"
    if total >= 65:
        return "C (仅观察)"
    return "淘汰"


def evaluate(f: dict, market_ok: bool, price_only: bool = False) -> ScoreResult:
    checks = hard_filters(f)
    passed = passes_hard_filter(checks)

    s_vcp = score_vcp(f)
    s_fund, fund_has_data = score_fundamentals(f)
    s_mkt = score_market(f, market_ok)
    s_rs = score_rs_trend(f)
    s_bo = score_breakout(f)
    s_inst = score_institution(f)
    s_rr = score_risk_reward(f)

    if price_only:
        # 纯价格模型：不用基本面(避免回测未来泄漏)，把基本面20分权重
        # 摊到技术维度(VCP/RS趋势/突破量价 各×放大系数)，总分仍归一到100
        s_fund = 0.0
        tech_raw = s_vcp + s_mkt + s_rs + s_bo + s_inst + s_rr  # 满分80
        total = round(tech_raw / 80 * 100, 1) if tech_raw else 0.0
    else:
        total = round(s_vcp + s_fund + s_mkt + s_rs + s_bo + s_inst + s_rr, 1)

    # 过热惩罚(非单调)：过热分越高，从总分扣越多，最多扣25分。
    # 让"太强/透支"的高分股被压下来,对应回测里高分档反而不涨的现象。
    oh = f.get("overheat", {}).get("overheat_score")
    if oh is not None:
        penalty = min(max(oh - 40, 0) / 60 * 25, 25)  # 过热分>40才开始扣
        total = round(max(total - penalty, 0), 1)

    grade = grade_of(total)
    if not passed:
        grade = "未过滤"  # 硬门槛未过，不进任何档

    gaps = []
    if not fund_has_data:
        gaps.append("基本面(EPS/营收)缺失，用中性分")
    if f["rs"] is None:
        gaps.append("RS无法计算(数据不足)")
    if f["hl52"].get("approx"):
        gaps.append(f"52周高低为近似({f['hl52'].get('window_days')}日窗口，免费源限制)")

    # 触发层
    p = f["pivot"]
    v = f["vcp"]
    vcp_trigger = (v["n_contractions"] >= 3 and v["is_decreasing"] and v["vol_dryup"]
                   and p.get("breakout") and p.get("vol_confirm")
                   and p.get("pct_over_pivot") is not None and p["pct_over_pivot"] <= 5)
    trigger = {
        "vcp_breakout": bool(vcp_trigger),
        "pocket_pivot": bool(f["pocket_pivot"]),
        "buyable_now": bool(vcp_trigger or (f["pocket_pivot"] and passed)),
    }

    return ScoreResult(
        ticker=f["ticker"], total=total, grade=grade,
        breakdown={"VCP结构": s_vcp, "基本面": s_fund, "大盘行业": s_mkt,
                   "RS趋势": s_rs, "突破量价": s_bo, "机构": s_inst, "风险收益": s_rr},
        passed_filter=passed, filter_checks=checks, trigger=trigger, data_gaps=gaps,
    )
