"""Point-in-time market-behavior sector classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math


RULE_VERSION = "market_behavior_v1"


@dataclass(frozen=True)
class MarketBehaviorResult:
    sector_key: str
    benchmark_ticker: str
    residual_correlation: float
    residual_beta: float
    relative_return_63d: float
    common_days: int
    confidence: float
    agrees_with_sec: bool
    conflict_reason: str
    rule_version: str
    asof: str


def _returns_by_date(rows, asof):
    cutoff = date.fromisoformat(str(asof))
    clean = {}
    for date_text, value in rows:
        parsed = date.fromisoformat(str(date_text))
        price = float(value)
        if parsed <= cutoff and math.isfinite(price) and price > 0:
            clean[parsed.isoformat()] = price
    result = {}
    previous = None
    for date_text in sorted(clean):
        price = clean[date_text]
        if previous is not None:
            result[date_text] = price / previous - 1.0
        previous = price
    return result


def _covariance(left, right):
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    return sum(
        (left[index] - left_mean) * (right[index] - right_mean)
        for index in range(len(left))
    ) / len(left)


def _variance(values):
    return _covariance(values, values)


def _correlation(left, right):
    denominator = math.sqrt(_variance(left) * _variance(right))
    if denominator <= 0:
        return 0.0
    return _covariance(left, right) / denominator


def _compound(values):
    value = 1.0
    for daily_return in values:
        value *= 1.0 + daily_return
    return value - 1.0


def classify_market_behavior(
    histories,
    ticker,
    sector_etfs,
    *,
    sec_sector,
    asof,
    min_observations=126,
    max_observations=252,
):
    ticker = str(ticker).strip().upper()
    if ticker not in histories or "SPY" not in histories:
        return None
    stock_returns = _returns_by_date(histories[ticker], asof)
    spy_returns = _returns_by_date(histories["SPY"], asof)
    candidates = []
    for sector_key, benchmark_ticker in sector_etfs.items():
        benchmark_ticker = str(benchmark_ticker).strip().upper()
        if benchmark_ticker not in histories:
            continue
        etf_returns = _returns_by_date(histories[benchmark_ticker], asof)
        common_dates = sorted(
            set(stock_returns) & set(spy_returns) & set(etf_returns)
        )[-int(max_observations) :]
        if len(common_dates) < int(min_observations):
            continue
        stock = [stock_returns[item] for item in common_dates]
        spy = [spy_returns[item] for item in common_dates]
        etf = [etf_returns[item] for item in common_dates]
        spy_variance = _variance(spy)
        if spy_variance <= 0:
            continue
        stock_market_beta = _covariance(stock, spy) / spy_variance
        etf_market_beta = _covariance(etf, spy) / spy_variance
        stock_residual = [
            stock[index] - stock_market_beta * spy[index]
            for index in range(len(common_dates))
        ]
        etf_residual = [
            etf[index] - etf_market_beta * spy[index]
            for index in range(len(common_dates))
        ]
        etf_residual_variance = _variance(etf_residual)
        if etf_residual_variance <= 0:
            continue
        correlation = _correlation(stock_residual, etf_residual)
        residual_beta = (
            _covariance(stock_residual, etf_residual)
            / etf_residual_variance
        )
        window = min(63, len(common_dates))
        relative_return = (
            _compound(stock[-window:]) - _compound(etf[-window:])
        )
        candidates.append(
            (
                correlation,
                str(sector_key),
                benchmark_ticker,
                residual_beta,
                relative_return,
                len(common_dates),
            )
        )
    if not candidates:
        return None
    (
        correlation,
        sector_key,
        benchmark_ticker,
        residual_beta,
        relative_return,
        common_days,
    ) = max(candidates, key=lambda row: (row[0], row[1]))
    coverage = min(1.0, common_days / float(max_observations))
    confidence = max(0.0, min(1.0, (correlation - 0.05) / 0.45)) * coverage
    agrees = str(sec_sector or "") == sector_key
    if agrees:
        conflict_reason = "与 SEC 基本面板块一致"
    else:
        conflict_reason = (
            f"SEC 基本面板块为 {sec_sector or '未分类'}，"
            f"价格行为更接近 {sector_key}（{benchmark_ticker}）"
        )
    return MarketBehaviorResult(
        sector_key=sector_key,
        benchmark_ticker=benchmark_ticker,
        residual_correlation=correlation,
        residual_beta=residual_beta,
        relative_return_63d=relative_return,
        common_days=common_days,
        confidence=confidence,
        agrees_with_sec=agrees,
        conflict_reason=conflict_reason,
        rule_version=RULE_VERSION,
        asof=str(asof),
    )


def write_market_behavior(connection, ticker, result):
    connection.execute(
        """
        INSERT INTO sector_classifications
            (ticker, taxonomy, sector_key, benchmark_ticker, confidence,
             source, rule_version, asof, residual_correlation, residual_beta,
             relative_return_63d, common_days, agrees_with_sec, conflict_reason)
        VALUES (?, 'market_behavior', ?, ?, ?, 'price_returns', ?, ?, ?, ?, ?,
                ?, ?, ?)
        ON CONFLICT(ticker, taxonomy, rule_version, asof) DO UPDATE SET
            sector_key=excluded.sector_key,
            benchmark_ticker=excluded.benchmark_ticker,
            confidence=excluded.confidence,
            residual_correlation=excluded.residual_correlation,
            residual_beta=excluded.residual_beta,
            relative_return_63d=excluded.relative_return_63d,
            common_days=excluded.common_days,
            agrees_with_sec=excluded.agrees_with_sec,
            conflict_reason=excluded.conflict_reason
        """,
        (
            str(ticker).strip().upper(),
            result.sector_key,
            result.benchmark_ticker,
            result.confidence,
            result.rule_version,
            result.asof,
            result.residual_correlation,
            result.residual_beta,
            result.relative_return_63d,
            result.common_days,
            int(result.agrees_with_sec),
            result.conflict_reason,
        ),
    )
