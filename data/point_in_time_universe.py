"""Strict normalization for point-in-time universe provider feeds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Optional


TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


@dataclass(frozen=True)
class HistoricalMembership:
    ticker: str
    security_name: str
    effective_from: str
    effective_to: Optional[str]
    is_active_now: bool
    is_delisted: bool


@dataclass(frozen=True)
class SymbolChange:
    old_symbol: str
    new_symbol: str
    effective_date: str
    exchange: str
    company_name: str


def normalize_historical_components(payload):
    """Normalize EODHD historical index components without guessing fields."""
    rows = _component_rows(payload)
    normalized = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise TypeError("historical component rows must be mappings")
        ticker = _ticker(raw.get("Code"), field="Code")
        effective_from = _iso_date(
            raw.get("StartDate"),
            field="StartDate",
        )
        effective_to = _optional_iso_date(
            raw.get("EndDate"),
            field="EndDate",
        )
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("EndDate must be later than StartDate")
        item = HistoricalMembership(
            ticker=ticker,
            security_name=str(raw.get("Name") or ticker).strip(),
            effective_from=effective_from,
            effective_to=effective_to,
            is_active_now=_boolean_flag(raw.get("IsActiveNow", 0)),
            is_delisted=_boolean_flag(raw.get("IsDelisted", 0)),
        )
        key = (ticker, effective_from)
        if key in normalized and normalized[key] != item:
            raise ValueError(f"conflicting historical component: {key}")
        normalized[key] = item
    return tuple(
        sorted(
            normalized.values(),
            key=lambda item: (item.ticker, item.effective_from),
        )
    )


def normalize_symbol_changes(payload):
    """Normalize EODHD US symbol changes as identity hints only."""
    if not isinstance(payload, list):
        raise TypeError("symbol change payload must be a list")
    if not payload:
        raise ValueError("symbol change payload must not be empty")
    normalized = {}
    for raw in payload:
        if not isinstance(raw, dict):
            raise TypeError("symbol change rows must be mappings")
        old_symbol = _ticker(raw.get("old_symbol"), field="old_symbol")
        new_symbol = _ticker(raw.get("new_symbol"), field="new_symbol")
        if old_symbol == new_symbol:
            raise ValueError("old_symbol and new_symbol must differ")
        effective_date = _iso_date(
            raw.get("effective"),
            field="effective",
        )
        item = SymbolChange(
            old_symbol=old_symbol,
            new_symbol=new_symbol,
            effective_date=effective_date,
            exchange=str(raw.get("exchange") or "US").strip().upper(),
            company_name=str(raw.get("company_name") or "").strip(),
        )
        key = (old_symbol, effective_date)
        if key in normalized and normalized[key] != item:
            raise ValueError(f"conflicting symbol change: {key}")
        normalized[key] = item
    return tuple(
        sorted(
            normalized.values(),
            key=lambda item: (item.effective_date, item.old_symbol),
        )
    )


def _component_rows(payload):
    if not isinstance(payload, dict):
        raise TypeError("historical component payload must be a mapping")
    nested = payload.get("HistoricalTickerComponents")
    source = nested if nested is not None else payload
    if not isinstance(source, dict):
        raise TypeError("HistoricalTickerComponents must be a mapping")
    rows = list(source.values())
    if not rows:
        raise ValueError("historical component payload must not be empty")
    return rows


def _ticker(value, *, field):
    ticker = str(value or "").strip().upper()
    if not TICKER_RE.fullmatch(ticker):
        raise ValueError(f"{field} is not a supported ticker")
    return ticker


def _iso_date(value, *, field):
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def _optional_iso_date(value, *, field):
    if value is None or str(value).strip() == "":
        return None
    return _iso_date(value, field=field)


def _boolean_flag(value):
    if isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1"):
        return str(value) == "1"
    raise ValueError("boolean flags must be 0 or 1")
