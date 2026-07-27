"""Contracts and audit summaries for full delisted-stock history backfills."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import re


BACKFILL_VERSION = "delisted_history_backfill_v1"
CANDIDATE_SCHEMA_VERSION = "delisted_history_backfill_candidates_v1"
CATALOG_SCHEMA_VERSION = "delisted_security_catalog_v1"
PURIFICATION_RULE_VERSION = "delisted_security_purification_v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def freeze_candidates(catalog, catalog_sha256, start, finish):
    """Freeze accepted purified securities before making network requests."""
    if not isinstance(catalog, Mapping):
        raise TypeError("catalog must be a mapping")
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("catalog schema version does not match")
    if catalog.get("rule_version") != PURIFICATION_RULE_VERSION:
        raise ValueError("catalog rule version does not match")
    catalog_sha256 = str(catalog_sha256 or "").strip().lower()
    if not SHA256_RE.fullmatch(catalog_sha256):
        raise ValueError("catalog SHA-256 must be 64 lowercase hex characters")
    start = _iso_date(start, "start")
    finish = _iso_date(finish, "finish")
    if start > finish:
        raise ValueError("start date must not be after finish date")
    securities = catalog.get("securities")
    if isinstance(securities, (str, bytes)) or not isinstance(
        securities, Sequence
    ):
        raise TypeError("catalog securities must be a sequence")

    frozen = []
    seen = set()
    for source in securities:
        if not isinstance(source, Mapping):
            raise TypeError("catalog securities must be mappings")
        if not (
            source.get("classification") == "accepted_common"
            and source.get("backfill_eligible") is True
        ):
            continue
        if source.get("rule_version") != PURIFICATION_RULE_VERSION:
            raise ValueError("security rule version does not match")
        ticker = str(source.get("ticker") or "").strip().upper()
        exchange = str(source.get("exchange") or "").strip().upper()
        if not ticker or not exchange:
            raise ValueError("eligible security requires ticker and exchange")
        key = (exchange, ticker)
        if key in seen:
            raise ValueError(
                f"duplicate eligible security: {exchange}/{ticker}"
            )
        seen.add(key)
        frozen.append(
            {
                "ticker": ticker,
                "name": str(source.get("name") or ticker).strip(),
                "exchange": exchange,
                "currency": str(source.get("currency") or "").strip().upper(),
                "provider_type": str(
                    source.get("provider_type") or ""
                ).strip(),
                "provider_isin": _optional_text(
                    source.get("provider_isin")
                ),
                "identity_status": str(
                    source.get("identity_status") or ""
                ).strip(),
                "identity_key": _optional_text(source.get("identity_key")),
                "classification": "accepted_common",
                "backfill_eligible": True,
                "rule_version": PURIFICATION_RULE_VERSION,
            }
        )
    frozen.sort(key=lambda row: (row["exchange"], row["ticker"]))
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "backfill_version": BACKFILL_VERSION,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "catalog_rule_version": PURIFICATION_RULE_VERSION,
        "catalog_sha256": catalog_sha256,
        "start_date": start,
        "finish_date": finish,
        "candidate_count": len(frozen),
        "securities": frozen,
    }


def summarize_backfill(candidates, audits):
    """Summarize a complete one-to-one audit of the frozen candidate set."""
    if not isinstance(candidates, Mapping):
        raise TypeError("candidates must be a mapping")
    if candidates.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("candidate schema version does not match")
    if candidates.get("backfill_version") != BACKFILL_VERSION:
        raise ValueError("candidate backfill version does not match")
    securities = tuple(candidates.get("securities") or ())
    audits = tuple(audits)
    expected = [_row_key(row) for row in securities]
    if len(set(expected)) != len(expected):
        raise ValueError("frozen candidates contain duplicate securities")
    actual = [_row_key(row) for row in audits]
    if len(set(actual)) != len(actual):
        raise ValueError("audits contain duplicate securities")
    if set(actual) != set(expected):
        raise ValueError("audits must match the frozen candidates")
    audit_by_key = {
        _row_key(row): row
        for row in audits
    }

    by_exchange = []
    for exchange in sorted({key[0] for key in expected}):
        exchange_audits = [
            audit_by_key[key] for key in expected if key[0] == exchange
        ]
        by_exchange.append(
            {
                "exchange": exchange,
                **_aggregate(exchange_audits),
            }
        )
    total = _aggregate(
        [audit_by_key[key] for key in expected]
    )
    total["completion_status"] = (
        "partial" if total["retryable_errors"] else "complete"
    )
    total["by_exchange"] = by_exchange
    return total


def _aggregate(audits):
    success = [
        row
        for row in audits
        if row.get("request_status") == "success"
        and _integer(row, "valid_rows") > 0
    ]
    errors = [
        row
        for row in audits
        if row.get("request_status") not in {"success", "empty"}
    ]
    return {
        "candidate_count": len(audits),
        "audited_count": len(audits),
        "usable_histories": len(success),
        "empty_responses": sum(
            row.get("request_status") == "empty" for row in audits
        ),
        "retryable_errors": sum(
            bool(row.get("retryable")) for row in errors
        ),
        "permanent_errors": sum(
            not bool(row.get("retryable")) for row in errors
        ),
        "quality_warnings": sum(
            row.get("quality_status") in {"warning", "no_valid_rows"}
            for row in audits
        ),
        "raw_rows": sum(_integer(row, "raw_rows") for row in audits),
        "valid_rows": sum(_integer(row, "valid_rows") for row in audits),
        "invalid_rows": sum(
            _integer(row, "invalid_rows") for row in audits
        ),
        "duplicate_dates": sum(
            _integer(row, "duplicate_dates") for row in audits
        ),
        "raw_bytes": sum(_integer(row, "raw_bytes") for row in audits),
    }


def _row_key(row):
    if not isinstance(row, Mapping):
        raise TypeError("candidate and audit rows must be mappings")
    exchange = str(row.get("exchange") or "").strip().upper()
    ticker = str(row.get("ticker") or "").strip().upper()
    if not exchange or not ticker:
        raise ValueError("candidate and audit rows require exchange and ticker")
    return exchange, ticker


def _integer(row, field):
    value = int(row.get(field) or 0)
    if value < 0:
        raise ValueError(f"{field} must not be negative")
    return value


def _iso_date(value, field):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def _optional_text(value):
    if value in (None, ""):
        return None
    return str(value).strip() or None
