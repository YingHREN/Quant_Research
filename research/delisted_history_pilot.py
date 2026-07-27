"""Deterministic sampling and audit logic for delisted-stock histories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re


SAMPLE_VERSION = "delisted_history_pilot_v1"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


def select_stratified_sample(catalog, quotas):
    """Select a stable exchange-stratified sample before history requests."""
    eligible = eligible_catalog_rows(catalog)
    checked_quotas = _validated_quotas(quotas)
    by_exchange = {}
    for row in eligible:
        by_exchange.setdefault(row["exchange"], []).append(row)

    selected = []
    for exchange, quota in checked_quotas.items():
        available = by_exchange.get(exchange, ())
        if len(available) < quota:
            raise ValueError(
                f"{exchange} has {len(available)} eligible rows; "
                f"{quota} required"
            )
        ranked = sorted(
            available,
            key=lambda row: (
                _selection_hash(exchange, row["ticker"]),
                row["ticker"],
            ),
        )
        for row in ranked[:quota]:
            selected.append(
                {
                    **row,
                    "selection_hash": _selection_hash(
                        exchange,
                        row["ticker"],
                    ),
                    "sample_version": SAMPLE_VERSION,
                }
            )
    return tuple(
        sorted(selected, key=lambda row: (row["exchange"], row["ticker"]))
    )


def eligible_catalog_rows(catalog):
    if isinstance(catalog, (str, bytes)) or not isinstance(catalog, Sequence):
        raise TypeError("catalog must be a sequence")
    seen = set()
    result = []
    for source in catalog:
        if not isinstance(source, Mapping):
            raise TypeError("catalog rows must be mappings")
        ticker = str(source.get("Code") or "").strip().upper()
        exchange = str(source.get("Exchange") or "").strip().upper()
        currency = str(source.get("Currency") or "").strip().upper()
        security_type = " ".join(
            str(source.get("Type") or "").strip().lower().split()
        )
        if (
            exchange not in {"NASDAQ", "NYSE", "NYSE MKT"}
            or currency != "USD"
            or security_type != "common stock"
            or not TICKER_RE.fullmatch(ticker)
        ):
            continue
        if ticker in seen:
            raise ValueError(f"duplicate eligible ticker: {ticker}")
        seen.add(ticker)
        result.append(
            {
                "ticker": ticker,
                "name": str(source.get("Name") or ticker).strip(),
                "exchange": exchange,
                "currency": currency,
                "security_type": "Common Stock",
                "isin": (
                    None
                    if source.get("Isin") in (None, "")
                    else str(source.get("Isin")).strip()
                ),
            }
        )
    return tuple(sorted(result, key=lambda row: (row["exchange"], row["ticker"])))


def _validated_quotas(quotas):
    if not isinstance(quotas, Mapping) or not quotas:
        raise ValueError("quotas must be a non-empty mapping")
    result = {}
    for exchange, value in quotas.items():
        normalized = str(exchange).strip().upper()
        if (
            normalized not in {"NASDAQ", "NYSE", "NYSE MKT"}
            or isinstance(value, bool)
            or int(value) <= 0
        ):
            raise ValueError("quotas contain an invalid exchange or count")
        result[normalized] = int(value)
    return result


def _selection_hash(exchange, ticker):
    value = f"{SAMPLE_VERSION}|{exchange}|{ticker}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()
