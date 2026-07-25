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
    "IGV",
    "XSW",
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
    fallback_benchmark_tickers: tuple[str, ...] = ()


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
_SOFTWARE = (
    "ADBE",
    "CRM",
    "NOW",
    "ORCL",
    "MSFT",
    "INTU",
    "PANW",
    "CRWD",
    "PLTR",
    "SNOW",
    "DDOG",
    "MDB",
    "TEAM",
    "ZS",
    "OKTA",
    "HUBS",
    "WDAY",
)

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
        "software": MarketGroup(
            key="software",
            label_key="market.group.software",
            benchmark_tickers=("IGV", "XSW"),
            constituent_tickers=_SOFTWARE,
            fallback_benchmark_tickers=("XLK",),
        ),
    }
)


def market_group(key: str) -> MarketGroup:
    try:
        return MARKET_GROUPS[str(key)]
    except KeyError as exc:
        raise ValueError("unsupported_market_group") from exc


def market_group_for_ticker(ticker: str) -> MarketGroup | None:
    normalized = str(ticker).strip().upper()
    for group in MARKET_GROUPS.values():
        if (
            normalized in group.constituent_tickers
            or normalized in group.related_tickers
        ):
            return group
    return None


def modeled_market_groups() -> tuple[MarketGroup, ...]:
    """Return groups with explicit stock membership for model context."""
    return tuple(
        group
        for group in MARKET_GROUPS.values()
        if group.constituent_tickers or group.related_tickers
    )
