"""Built-in dashboard factors backed by the project's existing calculations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

import pandas as pd

from factors.compute import (
    _atr,
    adr_pct,
    avg_dollar_volume,
    high_low_52w,
    moving_averages,
    overheat,
    pivot_breakout,
    pocket_pivot,
    rs_rating,
    tight_platform,
    vcp_analysis,
    volume_stats,
)
from research.momentum import momentum_features
from research.early_reversal import build_early_reversal_rows
from research.resistance import build_near_resistance_rows
from research.reversal import build_reversal_rows
from run import market_uptrend
from scoring.engine import evaluate
from web.contracts import iso_date
from web.factors.base import FactorGroup, freeze_i18n
from web.factors.registry import FactorRegistry
from web.services.analysis import AnalysisContext


@dataclass(frozen=True)
class BuiltinFactor:
    key: str
    label: str
    group: str
    direction: str
    description: str
    _compute: Callable[[AnalysisContext], Any]
    _format: Callable[[Any], str] = str
    version: str = "builtin-v1"
    methodology: str = "Computed point in time from local OHLCV history through the observation date."
    overview: bool = True
    percentile_eligible: bool = True
    window: str | None = None
    i18n: Mapping[str, Mapping[str, str]] | None = None

    def __post_init__(self):
        object.__setattr__(self, "i18n", freeze_i18n(self.i18n))

    def compute(self, context: AnalysisContext):
        return self._compute(context)

    def format(self, value):
        return self._format(value)


def _cached(context, key, function):
    return context.cached(f"builtin:{key}", function)


def _vcp(context):
    return _cached(
        context,
        "strict_vcp",
        lambda: _with_rejection_reason_code(vcp_analysis(context.history_asof())),
    )


def _platform(context):
    return _cached(
        context,
        "tight_platform",
        lambda: _with_rejection_reason_code(tight_platform(context.history_asof())),
    )


def _pivot(context):
    return _cached(context, "pivot", lambda: pivot_breakout(context.history_asof()))


def _overheat(context):
    return _cached(context, "overheat", lambda: overheat(context.history_asof()))


def _momentum(context):
    def calculate():
        benchmark = context.benchmark_asof()
        if benchmark is None or benchmark.empty:
            return {}
        return momentum_features(
            context.history_asof(), benchmark, context.observation_date
        )

    return _cached(context, "momentum", calculate)


def _legacy_inputs(context):
    def calculate():
        history = context.history_asof()
        benchmark = context.benchmark_asof()
        return {
            "ticker": context.ticker,
            "ma": moving_averages(history),
            "rs": rs_rating(history, benchmark),
            "hl52": high_low_52w(history),
            "adr_pct": adr_pct(history),
            "avg_dollar_vol": avg_dollar_volume(history),
            "volume": volume_stats(history),
            "pocket_pivot": pocket_pivot(history),
            "vcp": _vcp(context),
            "pivot": _pivot(context),
            "overheat": _overheat(context),
            "fundamentals": context.metadata.get("fundamentals", {}),
        }

    return _cached(context, "legacy_inputs", calculate)


def _legacy_score(context):
    benchmark = context.benchmark_asof()
    result = evaluate(
        _legacy_inputs(context),
        market_ok=market_uptrend(benchmark),
        price_only=False,
    )
    return result.total


def _chart_value(key):
    def compute(context):
        rows = build_chart_rows(context)
        return rows[-1][key] if rows else None

    return compute


def _momentum_value(key):
    return lambda context: _momentum(context).get(key)


def _dict_format(value):
    if value.get("reject_reason"):
        return f"Rejected: {value['reject_reason']}"
    if value.get("reason"):
        return f"Rejected: {value['reason']}"
    return "Detected"


_LEGACY_REJECTION_REASON_CODES = {
    "历史不足": "insufficient_history",
    "价格未站上MA50": "below_ma50",
    "价未站上MA50": "below_ma50",
    "MA50<MA200(非上升趋势)": "ma50_below_ma200",
    "MA50<MA200": "ma50_below_ma200",
    "距52周高>25%": "too_far_from_52_week_high",
    "距52周高>10%": "too_far_from_52_week_high",
    "近20日涨幅>12%(加速上涨非整理)": "accelerated_20_session_rise",
    "近20日涨幅>12%(加速上涨)": "accelerated_20_session_rise",
    "无合格base(深度/单边/长度不满足)": "no_qualifying_base",
    "base内峰谷不足": "insufficient_base_swings",
    "base内收缩腿<2": "insufficient_contraction_legs",
    "非横盘(净涨幅或效率比过高)": "not_sideways",
}


def _rejection_reason_code(reason):
    if reason in (None, ""):
        return None
    exact = _LEGACY_REJECTION_REASON_CODES.get(str(reason))
    if exact:
        return exact
    if str(reason).startswith("收缩腿未严格递减"):
        return "contractions_not_decreasing"
    if str(reason).startswith("区间宽度"):
        return "platform_too_wide"
    return None


def _with_rejection_reason_code(value):
    result = dict(value)
    reason = result.get("reject_reason")
    if reason is None:
        reason = result.get("reason")
    result["rejection_reason_code"] = _rejection_reason_code(reason)
    return result


def _percent(value):
    return f"{value:.2f}%"


def _ratio(value):
    return f"{value:.2f}x"


def _zh(label, description, methodology, window, direction):
    return {
        "label": label,
        "description": description,
        "methodology": methodology,
        "window": window,
        "direction": direction,
    }


FACTOR_ZH = {
    "close_vs_ema20_pct": _zh(
        "收盘价相对 EMA20", "收盘价相对时点一致的 20 日 EMA。",
        "收盘价除以 20 日指数移动平均线再减一，以百分比表示。",
        "20 个交易日", "数值越高表示收盘价高于 EMA20 的幅度越大。",
    ),
    "close_vs_sma50_pct": _zh(
        "收盘价相对 SMA50", "收盘价相对时点一致的 50 日均线。",
        "收盘价除以过去 50 日简单移动平均线再减一，以百分比表示。",
        "50 个交易日", "数值越高表示收盘价高于 SMA50 的幅度越大。",
    ),
    "close_vs_sma200_pct": _zh(
        "收盘价相对 SMA200", "收盘价相对时点一致的 200 日均线。",
        "收盘价除以过去 200 日简单移动平均线再减一，以百分比表示。",
        "200 个交易日", "数值越高表示收盘价高于 SMA200 的幅度越大。",
    ),
    "mom_3_1": _zh(
        "3-1 个月动量", "剔除最近一个月的三个月收益。",
        "截至观察日前 21 个交易日的时点一致 63 日收益。",
        "63 个交易日，跳过最近 21 个交易日", "数值越高表示历史动量越强。",
    ),
    "mom_6_1": _zh(
        "6-1 个月动量", "剔除最近一个月的六个月收益。",
        "截至观察日前 21 个交易日的时点一致 126 日收益。",
        "126 个交易日，跳过最近 21 个交易日", "数值越高表示历史动量越强。",
    ),
    "mom_12_1": _zh(
        "12-1 个月动量", "剔除最近一个月的十二个月收益。",
        "截至观察日前 21 个交易日的时点一致 252 日收益。",
        "252 个交易日，跳过最近 21 个交易日", "数值越高表示历史动量越强。",
    ),
    "strict_vcp": _zh(
        "向上突破准备形态（严格 VCP）",
        "VCP数学形态规则：高精度优先的向上突破准备诊断，包括拒绝原因。",
        "VCP数学形态规则评估趋势、基底深度、收缩阶段、成交量萎缩和延伸度。",
        "最多 250 个交易日；候选基底为 20 至 80 日", "中性诊断；不按数值高低判定优劣。",
    ),
    "tight_platform": _zh(
        "向上突破准备形态（紧密平台）",
        "VCP数学形态规则：高层紧密平台的向上突破准备诊断，包括拒绝原因。",
        "VCP数学形态规则评估趋势、高点接近度、20 日宽度、效率和成交量萎缩。",
        "20 个交易日", "中性诊断；不按数值高低判定优劣。",
    ),
    "pivot_distance_pct": _zh(
        "距枢轴点", "收盘价相对前 20 日枢轴点的距离。",
        "收盘价除以前 20 个交易日的最高收盘价再减一，以百分比表示。",
        "前 20 个交易日", "中性诊断；正值表示高于枢轴点，负值表示低于枢轴点。",
    ),
    "prior_high_breakout": _zh(
        "突破前高", "收盘价首次上穿此前 20 日最高收盘价。",
        "当前收盘价上穿不含当日的 20 日最高收盘价；只在交叉发生日为真。",
        "前 20 个交易日", "事件因子；需通过走步样本外检验预测能力。",
    ),
    "trendline_breakout": _zh(
        "突破下降趋势线", "收盘价上穿两个已确认下降摆动高点形成的阻力线。",
        "仅使用观察日之前已经确认的最近两个递减摆动高点外推阻力线。",
        "自适应摆动窗口", "事件因子；需通过走步样本外检验预测能力。",
    ),
    "higher_low_confirmed": _zh(
        "更高低点确认", "最新确认低点高于上一确认低点并超过 ATR 容差。",
        "摆动低点须经后续反弹确认，且高出上一低点至少 0.25 倍 ATR20。",
        "自适应摆动窗口", "事件因子；需通过走步样本外检验预测能力。",
    ),
    "reversal_signal_count": _zh(
        "向上结构反转条件数",
        "三条件价格结构规则模型：观察日同时触发的向上反转条件数量。",
        "三条件价格结构规则模型对突破前高、突破下降趋势线和更高低点确认三个布尔事件求和。",
        "观察日", "描述性组合；达到两个仅标记候选，不代表买入建议。",
    ),
    "early_reversal_score": _zh(
        "向上早期反转观察",
        "四条件规则评分模型：结构确认前的收盘后向上反转观察分数。",
        "四条件规则评分模型中，前一日放量下跌、当日价格接受、接近未突破的下降趋势线和当日量能支持各计 25 分；前两项必须同时满足且总分至少 75 才进入观察。",
        "前一交易日与观察日", "描述性观察分数；不是反转概率，也不替代结构确认。",
    ),
    "volume_ratio": _zh(
        "成交量比率", "当前成交量除以时点一致的 20 日平均成交量。",
        "当日成交量除以过去 20 日简单平均成交量。",
        "20 个交易日", "数值越高表示当前成交量参与度越高。",
    ),
    "atr20_pct": _zh(
        "ATR20", "20 日平均真实波幅占收盘价的百分比。",
        "标准 20 日平均真实波幅除以观察日收盘价，以百分比表示。",
        "20 个交易日", "数值越低表示历史价格波幅越小。",
    ),
    "realized_vol_63": _zh(
        "63 日已实现波动率", "最多使用 63 个时点一致日收益计算的年化波动率。",
        "最多 63 个日收盘收益的标准差乘以 252 的平方根进行年化。",
        "最多 63 个交易日", "数值越低表示历史已实现波动率越小。",
    ),
    "overheat_score": _zh(
        "过热", "现有的非单调延伸度和波动率诊断。",
        "由 ATR 标准化短期收益、均线延伸、连续走势和近期波幅构成的标准描述性综合指标。",
        "最多 200 个交易日，重点观察近期 3 至 20 日", "数值越低表示过热程度越低。",
    ),
    "legacy_score": _zh(
        "传统规则分数", "未经预测能力验证；仅保留为传统规则诊断。",
        "使用价格和基准输入按时点运行现有传统规则引擎；未经预测能力验证。",
        "由传统规则的多个历史窗口共同决定", "中性诊断；不用于预测性排序。",
    ),
}


FACTOR_WINDOWS = {
    "close_vs_ema20_pct": "20 sessions",
    "close_vs_sma50_pct": "50 sessions",
    "close_vs_sma200_pct": "200 sessions",
    "mom_3_1": "63 sessions, skipping the latest 21",
    "mom_6_1": "126 sessions, skipping the latest 21",
    "mom_12_1": "252 sessions, skipping the latest 21",
    "strict_vcp": "Up to 250 sessions; candidate bases span 20 to 80 sessions",
    "tight_platform": "20 sessions",
    "pivot_distance_pct": "Prior 20 sessions",
    "prior_high_breakout": "Prior 20 sessions",
    "trendline_breakout": "Adaptive confirmed swings",
    "higher_low_confirmed": "Adaptive confirmed swings",
    "reversal_signal_count": "Observation session",
    "early_reversal_score": "Prior session and observation session",
    "volume_ratio": "20 sessions",
    "atr20_pct": "20 sessions",
    "realized_vol_63": "Up to 63 sessions",
    "overheat_score": "Up to 200 sessions, emphasizing the latest 3 to 20",
    "legacy_score": "Multiple historical windows from the traditional rule set",
}


GROUP_ZH = {
    "trend": _zh("趋势", "价格相对移动平均线的位置诊断。", "均线位置诊断。", "20 至 200 个交易日", "通常数值越高表示趋势越强。"),
    "momentum": _zh("动量", "剔除最近一个月的历史收益诊断。", "剔除最近一个月的时点一致历史收益。", "63 至 252 个交易日", "数值越高表示历史动量越强。"),
    "structure": _zh("VCP / 结构", "价格收缩、平台和枢轴点结构诊断。", "标准严格 VCP、平台和枢轴点诊断。", "20 至 250 个交易日", "诊断方向因具体因子而异。"),
    "volume": _zh("成交量 / 价格", "当前成交量相对本地历史的参与度诊断。", "成交量参与度相对本地历史的诊断。", "20 个交易日", "通常数值越高表示成交量参与度越高。"),
    "risk": _zh("风险", "历史波幅、波动率和价格延伸诊断。", "波幅、波动率和延伸度诊断。", "20 至 200 个交易日", "通常数值越低表示历史风险或过热程度越低。"),
    "legacy": _zh("传统规则", "仅供比较的传统描述性规则输出。", "保留传统描述性规则输出，仅供比较。", "多个传统规则窗口", "中性诊断；不用于预测性排序。"),
}


def build_default_registry(max_peer_cache_size=4096):
    """Return the ordered first-party factor collection used by the dashboard."""
    factors = [
        BuiltinFactor("close_vs_ema20_pct", "Close vs EMA20", "trend", "higher",
                      "Close relative to the point-in-time 20-session EMA.",
                      lambda c: _distance_from(c, "ema20"), _percent,
                      methodology="Close divided by the 20-session exponential moving average, minus one, expressed in percent."),
        BuiltinFactor("close_vs_sma50_pct", "Close vs SMA50", "trend", "higher",
                      "Close relative to the point-in-time 50-session average.",
                      lambda c: _distance_from(c, "sma50"), _percent,
                      methodology="Close divided by the trailing 50-session simple moving average, minus one, expressed in percent."),
        BuiltinFactor("close_vs_sma200_pct", "Close vs SMA200", "trend", "higher",
                      "Close relative to the point-in-time 200-session average.",
                      lambda c: _distance_from(c, "sma200"), _percent,
                      methodology="Close divided by the trailing 200-session simple moving average, minus one, expressed in percent."),
        BuiltinFactor("mom_3_1", "3-1 month momentum", "momentum", "higher",
                      "Three-month return excluding the latest month.",
                      _momentum_value("mom_3_1"), lambda v: f"{v:.2%}",
                      methodology="Point-in-time 63-session return ending 21 sessions before the observation date."),
        BuiltinFactor("mom_6_1", "6-1 month momentum", "momentum", "higher",
                      "Six-month return excluding the latest month.",
                      _momentum_value("mom_6_1"), lambda v: f"{v:.2%}",
                      methodology="Point-in-time 126-session return ending 21 sessions before the observation date."),
        BuiltinFactor("mom_12_1", "12-1 month momentum", "momentum", "higher",
                      "Twelve-month return excluding the latest month.",
                      _momentum_value("mom_12_1"), lambda v: f"{v:.2%}",
                      methodology="Point-in-time 252-session return ending 21 sessions before the observation date."),
        BuiltinFactor("strict_vcp", "Bullish breakout setup (Strict VCP)", "structure", "neutral",
                      "VCP mathematical shape rules: a precision-first bullish-breakout setup diagnostic, including its rejection reason.",
                      _vcp, _dict_format,
                      methodology="VCP mathematical shape rules evaluate trend, base depth, contraction legs, volume dry-up, and extension."),
        BuiltinFactor("tight_platform", "Bullish breakout setup (tight platform)", "structure", "neutral",
                      "VCP mathematical shape rules: a high-level tight-platform bullish-breakout setup diagnostic, including its rejection reason.",
                      _platform, _dict_format,
                      methodology="VCP mathematical shape rules evaluate trend, high proximity, 20-session width, efficiency, and volume dry-up."),
        BuiltinFactor("pivot_distance_pct", "Distance to pivot", "structure", "neutral",
                      "Close distance from the prior 20-session pivot.",
                      _chart_value("pivot_distance_pct"), _percent,
                      methodology="Close divided by the highest close in the prior 20 sessions, minus one, expressed in percent."),
        BuiltinFactor("prior_high_breakout", "Prior-high breakout", "structure", "neutral",
                      "Crossing above the prior 20-session closing high.",
                      _chart_value("prior_high_breakout"), lambda v: "Yes" if v else "No",
                      methodology="True only when close crosses from at-or-below to above the trailing 20-session high, excluding the observation session."),
        BuiltinFactor("trendline_breakout", "Descending-trendline breakout", "structure", "neutral",
                      "Crossing above resistance fitted through two confirmed lower swing highs.",
                      _chart_value("trendline_breakout"), lambda v: "Yes" if v else "No",
                      methodology="Uses only swing highs whose reversal confirmation date is not later than the observation date."),
        BuiltinFactor("higher_low_confirmed", "Confirmed higher low", "structure", "neutral",
                      "A newly confirmed swing low above its predecessor by an ATR tolerance.",
                      _chart_value("higher_low_confirmed"), lambda v: "Yes" if v else "No",
                      methodology="Emitted on confirmation when the latest swing low exceeds the previous low by at least 0.25 ATR20."),
        BuiltinFactor("reversal_signal_count", "Bullish structural reversal condition count", "structure", "neutral",
                      "Three-condition price-structure rule model: number of same-session causal bullish reversal events.",
                      _chart_value("reversal_signal_count"), lambda v: f"{int(v)}/3",
                      methodology="The three-condition price-structure rule model sums prior-high breakout, descending-trendline breakout, and confirmed higher-low event flags."),
        BuiltinFactor("early_reversal_score", "Early bullish reversal watch", "structure", "neutral",
                      "Four-condition rule-scoring model: end-of-session bullish reversal observation before structural confirmation.",
                      _chart_value("early_reversal_score"), lambda v: f"{int(v)}/100",
                      methodology="The four-condition rule-scoring model awards 25 points each for a prior-session selloff, current price acceptance, proximity below an active descending trendline, and current volume support; the first two are required for a watch.",
                      percentile_eligible=False),
        BuiltinFactor("volume_ratio", "Volume ratio", "volume", "higher",
                      "Current volume divided by its point-in-time 20-session average.",
                      _volume_ratio, _ratio,
                      methodology="Session volume divided by the trailing 20-session simple average volume."),
        BuiltinFactor("atr20_pct", "ATR20", "risk", "lower",
                      "Twenty-session average true range as a percentage of close.",
                      lambda c: _atr_percent(c), _percent,
                      methodology="Canonical 20-session average true range divided by observation-date close and expressed in percent."),
        BuiltinFactor("realized_vol_63", "63-day realized volatility", "risk", "lower",
                      "Annualized volatility from up to 63 point-in-time daily returns.",
                      _momentum_value("realized_vol_63"), lambda v: f"{v:.2%}",
                      methodology="Standard deviation of up to 63 daily close returns annualized with the square root of 252."),
        BuiltinFactor("overheat_score", "Overheat", "risk", "lower",
                      "Existing non-monotonic extension and volatility diagnostic.",
                      lambda c: _overheat(c).get("overheat_score"), lambda v: f"{v:.1f}",
                      methodology="Canonical descriptive composite of ATR-normalized short returns, moving-average extension, streak, and recent range."),
        BuiltinFactor("legacy_score", "Traditional rules score", "legacy", "neutral",
                      "Not validated for prediction; retained only as a traditional-rule diagnostic.",
                      _legacy_score, lambda v: f"{v:.1f}",
                      methodology="Existing traditional rule engine evaluated point in time with price and benchmark inputs; not validated for prediction.",
                      overview=False, percentile_eligible=False),
    ]
    factors = [
        replace(
            factor,
            window=FACTOR_WINDOWS[factor.key],
            i18n={"zh-CN": FACTOR_ZH[factor.key]},
        )
        for factor in factors
    ]
    groups = [
        FactorGroup("trend", "Trend", "Moving-average position diagnostics.", True),
        FactorGroup("momentum", "Momentum", "Point-in-time trailing returns excluding the latest month.", True),
        FactorGroup("structure", "VCP / structure", "Canonical strict-VCP, platform, and pivot diagnostics.", True),
        FactorGroup("volume", "Volume / price", "Volume participation relative to trailing local history.", True),
        FactorGroup("risk", "Risk", "Range, volatility, and extension diagnostics.", True),
        FactorGroup("legacy", "Traditional rules", "Legacy descriptive rule output retained for comparison only.", False),
    ]
    groups = [
        replace(group, i18n={"zh-CN": GROUP_ZH[group.key]}) for group in groups
    ]
    return FactorRegistry(
        factors,
        group_metadata=groups,
        max_peer_cache_size=max_peer_cache_size,
    )


def _distance_from(context, average_key):
    history = context.history_asof()
    if history.empty:
        return None
    close = history["Close"].astype(float)
    if average_key == "ema20":
        average = close.ewm(span=20, adjust=False).mean().iloc[-1]
    elif average_key == "sma50":
        average = close.rolling(50).mean().iloc[-1]
    elif average_key == "sma200":
        average = close.rolling(200).mean().iloc[-1]
    else:
        raise ValueError(f"Unsupported moving average: {average_key}")
    average = _optional_float(average)
    return (
        None
        if average in (None, 0)
        else (float(close.iloc[-1]) / average - 1) * 100
    )


def _volume_ratio(context):
    history = context.history_asof()
    if history.empty:
        return None
    volume = history["Volume"].astype(float)
    average = _optional_float(volume.rolling(20).mean().iloc[-1])
    return None if average in (None, 0) else float(volume.iloc[-1]) / average


def _atr_percent(context):
    history = context.history_asof()
    if history.empty:
        return None
    value = _cached(context, "atr20", lambda: _atr(history, 20))
    close = float(history["Close"].iloc[-1])
    return None if value is None or close == 0 else value / close * 100


def _optional_float(value):
    return None if pd.isna(value) else float(value)


def build_chart_rows(context: AnalysisContext):
    """Build point-in-time chart and crosshair values without remote data access."""
    def calculate():
        history = context.history_asof()
        if history.empty:
            return []
        close = history["Close"].astype(float)
        high = history["High"].astype(float)
        low = history["Low"].astype(float)
        volume = history["Volume"].astype(float)
        previous_close = close.shift(1)
        daily_return = close.pct_change()
        volume_change = volume.pct_change()
        true_range = pd.concat(
            [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1)
        ema20 = close.ewm(span=20, adjust=False).mean()
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        atr20 = true_range.rolling(20).mean()
        volume_ma20 = volume.rolling(20).mean()
        volume_ratio = volume / volume_ma20
        volume_ratio_change = volume_ratio.diff()
        pivot = close.shift(1).rolling(20).max()
        pivot_distance = (close / pivot - 1) * 100
        pivot_distance_change = pivot_distance.diff()
        atr20.iloc[:20] = float("nan")
        pivot.iloc[:21] = float("nan")
        above_ema20 = close >= ema20
        above_sma50 = close >= sma50
        reversal_rows = build_reversal_rows(history)
        early_reversal_rows = build_early_reversal_rows(history, reversal_rows)
        resistance_rows = build_near_resistance_rows(history, reversal_rows)

        rows = []
        for position, (timestamp, source) in enumerate(history.iterrows()):
            row_close = float(source["Close"])
            row_pivot = _optional_float(pivot.iloc[position])
            crossed_ema20 = bool(
                position > 0
                and above_ema20.iloc[position] != above_ema20.iloc[position - 1]
            )
            crossed_sma50 = bool(
                position > 0
                and pd.notna(sma50.iloc[position - 1])
                and above_sma50.iloc[position] != above_sma50.iloc[position - 1]
            )
            rows.append(
                {
                    "time": iso_date(timestamp),
                    "open": float(source["Open"]),
                    "high": float(source["High"]),
                    "low": float(source["Low"]),
                    "close": row_close,
                    "volume": float(source["Volume"]),
                    "daily_return": _optional_float(daily_return.iloc[position]),
                    "true_range_pct": (
                        float(true_range.iloc[position] / row_close * 100)
                        if row_close
                        else None
                    ),
                    "volume_change": _optional_float(volume_change.iloc[position]),
                    "volume_ma20": _optional_float(volume_ma20.iloc[position]),
                    "volume_ratio": _optional_float(volume_ratio.iloc[position]),
                    "volume_ratio_change": _optional_float(
                        volume_ratio_change.iloc[position]
                    ),
                    "ema20": _optional_float(ema20.iloc[position]),
                    "sma50": _optional_float(sma50.iloc[position]),
                    "sma200": _optional_float(sma200.iloc[position]),
                    "atr20": _optional_float(atr20.iloc[position]),
                    "pivot": row_pivot,
                    "pivot_distance_pct": _optional_float(
                        pivot_distance.iloc[position]
                    ),
                    "pivot_distance_change_pct": _optional_float(
                        pivot_distance_change.iloc[position]
                    ),
                    "crossed_ema20": crossed_ema20,
                    "crossed_sma50": crossed_sma50,
                    "ema20_cross": (
                        "above" if above_ema20.iloc[position] else "below"
                    ) if crossed_ema20 else None,
                    "sma50_cross": (
                        "above" if above_sma50.iloc[position] else "below"
                    ) if crossed_sma50 else None,
                    **reversal_rows[position],
                    **early_reversal_rows[position],
                    **resistance_rows[position],
                }
            )
        return rows

    return _cached(context, "chart_rows", calculate)
