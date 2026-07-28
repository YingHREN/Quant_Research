"""Versioned market, sector, and thematic group metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
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


def resolved_market_groups(
    assignments,
    available_tickers,
) -> tuple[MarketGroup, ...]:
    """Return stable group definitions with point-in-time stock membership."""
    if assignments is None:
        return modeled_market_groups()
    if not isinstance(assignments, Mapping):
        raise TypeError("assignments must be a mapping or None")
    try:
        available = {
            str(ticker).strip().upper()
            for ticker in available_tickers
            if str(ticker).strip()
        }
    except TypeError as exc:
        raise TypeError("available_tickers must be iterable") from exc

    members = {key: set() for key in MARKET_GROUPS}
    seen = set()
    for raw_ticker, assignment in assignments.items():
        ticker = str(raw_ticker).strip().upper()
        if not ticker or ticker in seen or ticker not in available:
            continue
        seen.add(ticker)
        if not isinstance(assignment, Mapping):
            continue
        if assignment.get("state", "assigned") != "assigned":
            continue
        group_key = str(assignment.get("primary_model_group") or "").strip()
        if group_key in members:
            members[group_key].add(ticker)

    return tuple(
        replace(
            group,
            constituent_tickers=tuple(sorted(members[group.key])),
            related_tickers=tuple(
                ticker
                for ticker in group.related_tickers
                if ticker in available
            ),
        )
        for group in MARKET_GROUPS.values()
    )
