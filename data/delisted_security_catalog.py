"""Deterministic security-type purification for EODHD delisted catalogs."""

from __future__ import annotations

from collections.abc import Mapping
import re


RULE_VERSION = "delisted_security_purification_v1"
CATALOG_SCHEMA_VERSION = "delisted_security_catalog_v1"
PRIMARY_EXCHANGES = frozenset({"NASDAQ", "NYSE", "NYSE MKT"})
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

_NON_COMMON_RULES = (
    (
        "warrant_signal",
        re.compile(r"\bwarrants?\b", re.IGNORECASE),
        re.compile(r"-(?:WS|WT|W)$", re.IGNORECASE),
    ),
    (
        "unit_signal",
        re.compile(r"\bunits?\b", re.IGNORECASE),
        re.compile(r"-(?:U|UN)$", re.IGNORECASE),
    ),
    (
        "right_signal",
        re.compile(r"\brights?\b", re.IGNORECASE),
        re.compile(r"-(?:R|RT)$", re.IGNORECASE),
    ),
    (
        "preferred_signal",
        re.compile(
            r"\bpreferred\b|\bdepositary shares?\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        "debt_signal",
        re.compile(r"\bnotes?\b|\bbonds?\b|\bdebentures?\b", re.IGNORECASE),
        None,
    ),
    (
        "fund_signal",
        re.compile(
            r"\bETF\b|\bexchange[- ]traded fund\b|\bclosed[- ]end fund\b",
            re.IGNORECASE,
        ),
        None,
    ),
)
_TEST_SECURITY_RE = re.compile(
    r"\btest (?:security|issue|stock)\b|^CTEST(?:-|$)",
    re.IGNORECASE,
)


def valid_isin(value):
    """Return whether a value is a syntactically valid ISO 6166 ISIN."""
    text = str(value or "").strip().upper()
    if not ISIN_RE.fullmatch(text):
        return False
    digits = "".join(
        str(ord(character) - ord("A") + 10)
        if character.isalpha()
        else character
        for character in text
    )
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        number = int(character)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def classify_catalog_row(raw):
    """Classify one provider row without inventing security identity."""
    if not isinstance(raw, Mapping):
        raise TypeError("catalog row must be a mapping")
    ticker = str(raw.get("Code") or "").strip().upper()
    name = str(raw.get("Name") or "").strip()
    exchange = str(raw.get("Exchange") or "").strip().upper()
    currency = str(raw.get("Currency") or "").strip().upper()
    provider_type = " ".join(
        str(raw.get("Type") or "").strip().split()
    )
    normalized_type = provider_type.lower()
    provider_isin = str(raw.get("Isin") or "").strip().upper() or None

    scope_reasons = []
    if exchange not in PRIMARY_EXCHANGES:
        scope_reasons.append("unsupported_exchange")
    if currency != "USD":
        scope_reasons.append("non_usd")
    if normalized_type != "common stock":
        scope_reasons.append("provider_type_not_common")
    if not TICKER_RE.fullmatch(ticker):
        scope_reasons.append("invalid_ticker")

    if provider_isin is None:
        identity_status = "ticker_only"
        identity_key = None
    elif valid_isin(provider_isin):
        identity_status = "strong_isin"
        identity_key = f"isin:{provider_isin}"
    else:
        identity_status = "invalid_isin"
        identity_key = None

    evidence = []
    non_common_reasons = []
    for reason, name_pattern, ticker_pattern in _NON_COMMON_RULES:
        name_match = name_pattern.search(name)
        ticker_match = (
            ticker_pattern.search(ticker)
            if ticker_pattern is not None
            else None
        )
        if name_match or ticker_match:
            non_common_reasons.append(reason)
            if name_match:
                evidence.append(f"name:{name_match.group(0)}")
            if ticker_match:
                evidence.append(f"ticker:{ticker_match.group(0)}")

    review_reasons = []
    if not name or name.upper() == ticker or _TEST_SECURITY_RE.search(name):
        review_reasons.append("ambiguous_name")
    if identity_status == "invalid_isin":
        review_reasons.append("invalid_isin")

    if scope_reasons:
        scope_status = "out_of_scope"
        classification = "out_of_scope"
        reasons = scope_reasons
    elif non_common_reasons:
        scope_status = "in_scope"
        classification = "rejected_non_common"
        reasons = non_common_reasons
    elif review_reasons:
        scope_status = "in_scope"
        classification = "needs_review"
        reasons = review_reasons
    else:
        scope_status = "in_scope"
        classification = "accepted_common"
        reasons = []

    return {
        "ticker": ticker,
        "name": name,
        "exchange": exchange,
        "currency": currency,
        "provider_type": provider_type,
        "provider_isin": provider_isin,
        "source_fields": {
            field: raw.get(field)
            for field in (
                "Code",
                "Name",
                "Exchange",
                "Currency",
                "Type",
                "Isin",
            )
        },
        "scope_status": scope_status,
        "classification": classification,
        "reason_codes": sorted(set(reasons)),
        "evidence": sorted(set(evidence)),
        "identity_status": identity_status,
        "identity_key": identity_key,
        "backfill_eligible": classification == "accepted_common",
        "rule_version": RULE_VERSION,
    }
