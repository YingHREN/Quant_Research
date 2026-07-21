"""涨跌预测模型：把评分引擎的结果映射为方向/概率/预期。

第一版实现 Codex 设计的"研究型映射"(未校准)：
- 用打分层的总分 + 关键因子 → 方向标签 + 上涨概率区间
- 带否决项(大盘转空/超pivot/量不足)

后续 backtest 会用历史数据校准这个映射(logistic)，替换掉这里的经验区间。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Prediction:
    direction: str        # 偏多 / 弱偏多 / 震荡 / 弱偏空 / 偏空
    up_prob: float        # 上涨概率 0~1
    expected_move: float  # 预期中位涨幅 %(粗略)
    confidence: str       # 高/中/低
    vetoes: list          # 触发的否决项


def _base_direction(total: float) -> str:
    if total >= 80:
        return "偏多"
    if total >= 70:
        return "弱偏多"
    if total >= 55:
        return "震荡"
    if total >= 45:
        return "弱偏空"
    return "偏空"


def _base_prob(total: float) -> float:
    """Codex 研究型映射：总分 → 初始上涨概率(未校准)。"""
    if total >= 90:
        return 0.74
    if total >= 80:
        return 0.66
    if total >= 70:
        return 0.585
    if total >= 55:
        return 0.50
    if total >= 45:
        return 0.42
    return 0.35


def predict(score_result, factors: dict, market_ok: bool) -> Prediction:
    total = score_result.total
    direction = _base_direction(total)
    up_prob = _base_prob(total)
    vetoes = []

    p = factors["pivot"]
    vol = factors["volume"]

    # 否决项(压过方向)
    if not market_ok:
        vetoes.append("大盘未确认上升→最高只能弱偏多")
        if direction == "偏多":
            direction = "弱偏多"
        up_prob = min(up_prob, 0.55)
    if p.get("pct_over_pivot") is not None and p["pct_over_pivot"] > 5:
        vetoes.append("超pivot5%→不可追入")
        up_prob = min(up_prob, 0.5)
    if vol.get("vol_ratio") and vol["vol_ratio"] < 1.0 and p.get("breakout"):
        vetoes.append("突破但缩量→不给强多")
        if direction == "偏多":
            direction = "弱偏多"

    # 预期涨幅：粗略用 ADR × 方向系数(后续回测替换)
    adr = factors.get("adr_pct") or 3.0
    coef = {"偏多": 3.0, "弱偏多": 1.5, "震荡": 0.0, "弱偏空": -1.5, "偏空": -3.0}[direction]
    expected_move = round(adr * coef, 1)

    # 置信度：数据完整性 + 是否有否决
    conf = "高"
    if score_result.data_gaps:
        conf = "中"
    if vetoes or factors["rs"] is None:
        conf = "低" if conf == "中" else "中"

    return Prediction(direction=direction, up_prob=round(up_prob, 3),
                      expected_move=expected_move, confidence=conf, vetoes=vetoes)
