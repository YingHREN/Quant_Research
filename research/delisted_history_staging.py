"""Pure validation and conservation helpers for delisted-history staging."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import json
import math


STAGING_SCHEMA_VERSION = "delisted_research_prices_v1"
STAGING_IMPORT_VERSION = "delisted_history_staging_import_v1"


@dataclass(frozen=True)
class RejectedDailyRow:
    source_index: int
    reason: str
    raw_json: str


def partition_history_rows(payload):
    """Partition provider rows using the same ordering as backfill audit."""
    if not isinstance(payload, list):
        raise TypeError("history payload must be a list")
    valid = []
    rejected = []
    seen_dates = set()
    for source_index, source in enumerate(payload):
        reason = None
        date_text = None
        if not isinstance(source, Mapping):
            reason = "invalid_row"
        else:
            try:
                date_text = date.fromisoformat(
                    str(source.get("date") or "")
                ).isoformat()
            except ValueError:
                reason = "invalid_date"
            if reason is None and date_text in seen_dates:
                reason = "duplicate_date"
            elif reason is None:
                seen_dates.add(date_text)
                try:
                    values = {
                        field: _finite(source.get(field))
                        for field in (
                            "open",
                            "high",
                            "low",
                            "close",
                            "adjusted_close",
                            "volume",
                        )
                    }
                except ValueError:
                    reason = "invalid_numeric"
                else:
                    if min(
                        values["open"],
                        values["high"],
                        values["low"],
                        values["close"],
                        values["adjusted_close"],
                    ) <= 0:
                        reason = "non_positive_price"
                    elif values["volume"] < 0:
                        reason = "negative_volume"
                    elif (
                        values["low"]
                        > min(values["open"], values["close"])
                        or values["high"]
                        < max(values["open"], values["close"])
                        or values["low"] > values["high"]
                    ):
                        reason = "invalid_ohlc"
        if reason is None:
            valid.append(source)
        else:
            rejected.append(
                RejectedDailyRow(
                    source_index=source_index,
                    reason=reason,
                    raw_json=json.dumps(
                        source,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
    return valid, rejected


def summarize_partitions(partitions):
    """Aggregate per-security partitions while enforcing row conservation."""
    if isinstance(partitions, (str, bytes)) or not isinstance(
        partitions, Sequence
    ):
        raise TypeError("partitions must be a sequence")
    totals = _empty_totals()
    by_exchange = {}
    seen = set()
    for row in partitions:
        if not isinstance(row, Mapping):
            raise TypeError("partition rows must be mappings")
        exchange = str(row.get("exchange") or "").strip().upper()
        ticker = str(row.get("ticker") or "").strip().upper()
        key = (exchange, ticker)
        if not exchange or not ticker or key in seen:
            raise ValueError("partition securities must be unique and complete")
        seen.add(key)
        raw_rows = _count(row, "raw_rows")
        valid_rows = _count(row, "valid_rows")
        rejected_rows = _count(row, "rejected_rows")
        if raw_rows != valid_rows + rejected_rows:
            raise ValueError(f"partition does not conserve rows: {ticker}")
        reasons = Counter(row.get("reason_counts") or {})
        if any(int(value) < 0 for value in reasons.values()):
            raise ValueError("reason counts must not be negative")
        if sum(int(value) for value in reasons.values()) != rejected_rows:
            raise ValueError(f"reasons do not conserve rejected rows: {ticker}")
        exchange_totals = by_exchange.setdefault(
            exchange,
            _empty_totals(),
        )
        for target in (totals, exchange_totals):
            target["security_count"] += 1
            target["raw_rows"] += raw_rows
            target["valid_rows"] += valid_rows
            target["rejected_rows"] += rejected_rows
            target["_reasons"].update(reasons)
    result = _finalize(totals)
    result["by_exchange"] = {
        exchange: _finalize(values)
        for exchange, values in sorted(by_exchange.items())
    }
    return result


def _finite(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("number must be finite")
    return number


def _count(row, field):
    value = int(row.get(field) or 0)
    if value < 0:
        raise ValueError(f"{field} must not be negative")
    return value


def _empty_totals():
    return {
        "security_count": 0,
        "raw_rows": 0,
        "valid_rows": 0,
        "rejected_rows": 0,
        "_reasons": Counter(),
    }


def _finalize(values):
    return {
        "security_count": values["security_count"],
        "raw_rows": values["raw_rows"],
        "valid_rows": values["valid_rows"],
        "rejected_rows": values["rejected_rows"],
        "reason_counts": dict(sorted(values["_reasons"].items())),
    }
