"""Deterministic sampling and audit logic for delisted-stock histories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
import math
import re


SAMPLE_VERSION = "delisted_history_pilot_v1"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
SUSPICIOUS_LABEL_RE = re.compile(
    r"(?:\bWARRANTS?\b|\bUNITS?\b|\bPREFERRED\b|"
    r"-(?:WT|WS|U|UN|RT|R)$)",
    re.IGNORECASE,
)


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


def audit_history_rows(sample_row, payload, *, raw_bytes):
    """Audit provider rows without silently deleting a whole response."""
    if not isinstance(sample_row, Mapping):
        raise TypeError("sample_row must be a mapping")
    if not isinstance(payload, list):
        raise TypeError("history payload must be a list")
    ticker = str(sample_row.get("ticker") or "").strip().upper()
    exchange = str(sample_row.get("exchange") or "").strip().upper()
    name = str(sample_row.get("name") or ticker).strip()
    seen_dates = set()
    valid_dates = []
    duplicate_dates = 0
    invalid_rows = 0
    for raw in payload:
        if not isinstance(raw, Mapping):
            invalid_rows += 1
            continue
        try:
            date_text = date.fromisoformat(
                str(raw.get("date") or "")
            ).isoformat()
        except ValueError:
            invalid_rows += 1
            continue
        if date_text in seen_dates:
            duplicate_dates += 1
            continue
        seen_dates.add(date_text)
        try:
            values = {
                field: _finite_value(raw.get(field))
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
            invalid_rows += 1
            continue
        if (
            min(
                values["open"],
                values["high"],
                values["low"],
                values["close"],
                values["adjusted_close"],
            )
            <= 0.0
            or values["volume"] < 0.0
            or values["low"] > min(values["open"], values["close"])
            or values["high"] < max(values["open"], values["close"])
            or values["low"] > values["high"]
        ):
            invalid_rows += 1
            continue
        valid_dates.append(date_text)
    valid_dates.sort()
    post_2018 = sum(item >= "2018-01-01" for item in valid_dates)
    if not payload:
        request_status = "empty"
        quality_status = "no_rows"
    else:
        request_status = "success"
        quality_status = (
            "clean"
            if duplicate_dates == 0 and invalid_rows == 0
            else "warning"
        )
        if not valid_dates:
            quality_status = "no_valid_rows"
    return {
        "ticker": ticker,
        "name": name,
        "exchange": exchange,
        "request_status": request_status,
        "quality_status": quality_status,
        "raw_rows": len(payload),
        "valid_rows": len(valid_dates),
        "duplicate_dates": duplicate_dates,
        "invalid_rows": invalid_rows,
        "first_date": valid_dates[0] if valid_dates else None,
        "last_date": valid_dates[-1] if valid_dates else None,
        "post_2018_valid_rows": post_2018,
        "traded_since_2018": post_2018 > 0,
        "raw_bytes": int(raw_bytes),
        "suspicious_security_label": bool(
            SUSPICIOUS_LABEL_RE.search(f"{ticker} {name}")
        ),
    }


def summarize_pilot(sample, audits, catalog):
    """Estimate full primary-exchange backfill from frozen sample evidence."""
    sample = tuple(sample)
    audits = tuple(audits)
    sample_tickers = [str(row["ticker"]) for row in sample]
    if len(set(sample_tickers)) != len(sample_tickers):
        raise ValueError("sample contains duplicate tickers")
    audit_by_ticker = {str(row["ticker"]): row for row in audits}
    if set(audit_by_ticker) != set(sample_tickers):
        raise ValueError("audits must match the frozen sample")
    eligible = eligible_catalog_rows(catalog)
    exchanges = sorted({str(row["exchange"]) for row in sample})
    rows = []
    for exchange in exchanges:
        exchange_sample = [
            row for row in sample if str(row["exchange"]) == exchange
        ]
        exchange_audits = [
            audit_by_ticker[str(row["ticker"])]
            for row in exchange_sample
        ]
        candidates = sum(
            str(row["exchange"]) == exchange for row in eligible
        )
        usable = [
            row
            for row in exchange_audits
            if row.get("request_status") == "success"
            and int(row.get("valid_rows") or 0) > 0
        ]
        usable_rate = len(usable) / float(len(exchange_sample))
        estimated_tickers = int(round(candidates * usable_rate))
        byte_values = [int(row["raw_bytes"]) for row in usable]
        row_values = [int(row["valid_rows"]) for row in usable]
        mean_bytes = _mean(byte_values)
        mean_rows = _mean(row_values)
        p90_bytes = _percentile_nearest_rank(byte_values, 0.90)
        p90_rows = _percentile_nearest_rank(row_values, 0.90)
        rows.append(
            {
                "exchange": exchange,
                "eligible_catalog": candidates,
                "sample_count": len(exchange_sample),
                "usable_histories": len(usable),
                "success_rate": usable_rate,
                "traded_since_2018_rate": sum(
                    bool(row.get("traded_since_2018"))
                    for row in exchange_audits
                )
                / float(len(exchange_sample)),
                "empty_responses": sum(
                    row.get("request_status") == "empty"
                    for row in exchange_audits
                ),
                "error_responses": sum(
                    row.get("request_status") not in {"success", "empty"}
                    for row in exchange_audits
                ),
                "suspicious_labels": sum(
                    bool(row.get("suspicious_security_label"))
                    for row in exchange_audits
                ),
                "mean_valid_rows": mean_rows,
                "p90_valid_rows": p90_rows,
                "mean_raw_bytes": mean_bytes,
                "p90_raw_bytes": p90_bytes,
                "estimated_successful_tickers": estimated_tickers,
                "estimated_valid_rows_mean": int(
                    round(estimated_tickers * mean_rows)
                ),
                "estimated_valid_rows_p90": int(
                    round(estimated_tickers * p90_rows)
                ),
                "estimated_raw_bytes_mean": int(
                    round(estimated_tickers * mean_bytes)
                ),
                "estimated_raw_bytes_p90": int(
                    round(estimated_tickers * p90_bytes)
                ),
            }
        )
    return {
        "sample_count": len(sample),
        "by_exchange": rows,
        "estimated_successful_tickers": sum(
            row["estimated_successful_tickers"] for row in rows
        ),
        "estimated_valid_rows_mean": sum(
            row["estimated_valid_rows_mean"] for row in rows
        ),
        "estimated_valid_rows_p90": sum(
            row["estimated_valid_rows_p90"] for row in rows
        ),
        "estimated_raw_bytes_mean": sum(
            row["estimated_raw_bytes_mean"] for row in rows
        ),
        "estimated_raw_bytes_p90": sum(
            row["estimated_raw_bytes_p90"] for row in rows
        ),
    }


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


def _finite_value(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid numeric value") from exc
    if not math.isfinite(number):
        raise ValueError("invalid numeric value")
    return number


def _mean(values):
    return 0.0 if not values else sum(values) / float(len(values))


def _percentile_nearest_rank(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, int(math.ceil(float(quantile) * len(ordered))) - 1)
    return float(ordered[index])
