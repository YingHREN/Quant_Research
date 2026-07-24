"""Versioned market, sector, and thematic group metadata."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


REFERENCE_TICKERS = (
    "SPY",
    "QQQ",
    "XLK",
    "XLC",
    "XLY",
    "XLP",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLB",
    "XLRE",
    "XLU",
    "SOXX",
    "SMH",
)

SECTOR_ETFS = MappingProxyType(
    {
        "technology": "XLK",
        "communication": "XLC",
        "consumer_discretionary": "XLY",
        "consumer_staples": "XLP",
        "energy": "XLE",
        "financials": "XLF",
        "health_care": "XLV",
        "industrials": "XLI",
        "materials": "XLB",
        "real_estate": "XLRE",
        "utilities": "XLU",
    }
)


@dataclass(frozen=True)
class MarketGroup:
    key: str
    label_key: str
    benchmark_tickers: tuple[str, ...]
    constituent_tickers: tuple[str, ...]
    related_tickers: tuple[str, ...] = ()


_SEMICONDUCTORS = (
    "NVDA",
    "AMD",
    "AVGO",
    "MU",
    "INTC",
    "QCOM",
    "TXN",
    "ADI",
    "MCHP",
    "MRVL",
    "ON",
    "NXPI",
    "AMAT",
    "LRCX",
    "KLAC",
    "TER",
    "ENTG",
)
_AI_INFRASTRUCTURE = ("NBIS", "ANET", "DELL", "HPE", "SMCI")

_PROXY_GROUPS = {
    key: MarketGroup(
        key=key,
        label_key=f"market.sector.{key}",
        benchmark_tickers=(ticker,),
        constituent_tickers=(),
    )
    for key, ticker in SECTOR_ETFS.items()
}
MARKET_GROUPS = MappingProxyType(
    {
        **_PROXY_GROUPS,
        "semiconductor": MarketGroup(
            key="semiconductor",
            label_key="market.group.semiconductor",
            benchmark_tickers=("SOXX", "SMH"),
            constituent_tickers=_SEMICONDUCTORS,
            related_tickers=_AI_INFRASTRUCTURE,
        ),
    }
)


def market_group(key: str) -> MarketGroup:
    try:
        return MARKET_GROUPS[str(key)]
    except KeyError as exc:
        raise ValueError("unsupported_market_group") from exc

