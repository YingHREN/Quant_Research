"""Deterministic security-type purification for EODHD delisted catalogs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
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
        re.compile(
            r"\bunits?\s*(?:$|EX\b)|\b(?:corporate|preferred) units?\b",
            re.IGNORECASE,
        ),
        re.compile(r"-(?:U|UN)$", re.IGNORECASE),
    ),
    (
        "right_signal",
        re.compile(r"\brights?\s*(?:$|to\b)", re.IGNORECASE),
        re.compile(r"-(?:R|RT)$", re.IGNORECASE),
    ),
    (
        "preferred_signal",
        re.compile(
            r"\bpreferred\s+(?:stock|shares?|units?|series)\b|"
            r"\bseries\s+[A-Z0-9.-]+(?:\s+\w+){0,4}\s+preferred\b|"
            r"\bparticipating preferred\b|"
            r"\bdepositary shares?\b.*\bpreferred\b|"
            r"\bpreferred\b.*\bdepositary shares?\b|"
            r"\bpreferred\s*$",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        "debt_signal",
        re.compile(
            r"\b(?:senior|subordinated|exchange[- ]traded)"
            r"(?:\s+\w+){0,3}\s+notes?\b|"
            r"\bnotes?\s+(?:due|expiry|\(cbt\))\b|"
            r"\bdebentures?\b|"
            r"\bbonds?\s+(?:fund|trust|\(cbt\)|due)\b|"
            r"\d(?:[\d .]*\d)?%\s+notes?\s*$",
            re.IGNORECASE,
        ),
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


def build_delisted_catalog(rows):
    """Build an order-independent catalog and quarantine identity conflicts."""
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise TypeError("catalog rows must be a sequence")
    securities = [classify_catalog_row(raw) for raw in rows]
    seen_in_scope = set()
    for item in securities:
        if item["scope_status"] != "in_scope":
            continue
        key = (item["exchange"], item["ticker"])
        if key in seen_in_scope:
            raise ValueError(
                f"duplicate in-scope security: {item['exchange']} "
                f"{item['ticker']}"
            )
        seen_in_scope.add(key)

    by_identity = {}
    for index, item in enumerate(securities):
        identity_key = item.get("identity_key")
        if identity_key:
            by_identity.setdefault(identity_key, []).append(index)
    for indexes in by_identity.values():
        if len(indexes) < 2:
            continue
        names = {
            _normalized_name(securities[index]["name"])
            for index in indexes
        }
        classifications = {
            securities[index]["classification"] for index in indexes
        }
        if len(names) == 1 and len(classifications) == 1:
            continue
        for index in indexes:
            item = dict(securities[index])
            item["reason_codes"] = sorted(
                set(item["reason_codes"]) | {"identity_conflict"}
            )
            item["evidence"] = sorted(
                set(item["evidence"]) | {f"identity:{item['identity_key']}"}
            )
            item["identity_status"] = "conflicting_isin"
            item["identity_key"] = None
            securities[index] = item

    securities.sort(
        key=lambda item: (
            item["scope_status"] == "out_of_scope",
            item["exchange"],
            item["ticker"],
            item["name"],
        )
    )
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "input_rows": len(rows),
        "securities": securities,
    }


def summarize_delisted_catalog(catalog):
    """Summarize a purified catalog without copying provider payloads."""
    if not isinstance(catalog, Mapping):
        raise TypeError("catalog must be a mapping")
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported delisted catalog schema")
    securities = catalog.get("securities")
    if not isinstance(securities, list):
        raise TypeError("catalog securities must be a list")
    classification_counts = Counter(
        item["classification"] for item in securities
    )
    identity_counts = Counter(item["identity_status"] for item in securities)
    in_scope_identity_counts = Counter(
        item["identity_status"]
        for item in securities
        if item["scope_status"] == "in_scope"
    )
    reason_counts = Counter(
        reason
        for item in securities
        for reason in item["reason_codes"]
    )
    reason_tickers = {}
    by_exchange = {}
    for item in securities:
        exchange_counts = by_exchange.setdefault(item["exchange"], Counter())
        exchange_counts[item["classification"]] += 1
        for reason in item["reason_codes"]:
            reason_tickers.setdefault(reason, set()).add(item["ticker"])
    return {
        "input_rows": len(securities),
        "in_scope_rows": sum(
            item["scope_status"] == "in_scope" for item in securities
        ),
        "backfill_eligible_rows": sum(
            bool(item["backfill_eligible"]) for item in securities
        ),
        "classification_counts": _sorted_counter(classification_counts),
        "identity_status_counts": _sorted_counter(identity_counts),
        "in_scope_identity_status_counts": _sorted_counter(
            in_scope_identity_counts
        ),
        "reason_counts": _sorted_counter(reason_counts),
        "reason_samples": {
            reason: sorted(tickers)[:5]
            for reason, tickers in sorted(reason_tickers.items())
        },
        "by_exchange": {
            exchange: _sorted_counter(counts)
            for exchange, counts in sorted(by_exchange.items())
        },
    }


def _normalized_name(value):
    return " ".join(str(value or "").upper().split())


def _sorted_counter(counter):
    return {key: int(counter[key]) for key in sorted(counter)}
