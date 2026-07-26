"""Normalized, provenance-rich research price storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
import sqlite3


SCHEMA_VERSION = "research_prices_v1"
ADJUSTMENT_METHOD = "eodhd_adjusted_close_ratio_v1"
DEFAULT_GAP_DAYS = 180


@dataclass(frozen=True)
class ImportSummary:
    ticker: str
    daily_rows: int
    segment_count: int
    split_rows: int
    dividend_rows: int


def _finite_number(value, field):
    if isinstance(value, bool):
        raise ValueError(f"invalid {field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not math.isfinite(number):
        raise ValueError(f"invalid {field}")
    return number


def normalize_daily_rows(rows, *, gap_days=DEFAULT_GAP_DAYS):
    """Validate and normalize EODHD rows without losing raw OHLC values."""
    if not isinstance(rows, list):
        raise ValueError("daily rows must be a list")
    ordered_rows = sorted(rows, key=lambda row: str(row.get("date") or ""))
    while ordered_rows and _is_all_zero_placeholder(ordered_rows[0]):
        ordered_rows.pop(0)
    normalized = []
    seen_dates = set()
    previous_date = None
    segment_id = 1
    segment_rows = []
    segments = []

    for source in ordered_rows:
        try:
            row_date = date.fromisoformat(str(source.get("date")))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid date") from exc
        date_text = row_date.isoformat()
        if date_text in seen_dates:
            raise ValueError(f"duplicate date: {date_text}")
        seen_dates.add(date_text)

        raw_open = _finite_number(source.get("open"), "raw open")
        raw_high = _finite_number(source.get("high"), "raw high")
        raw_low = _finite_number(source.get("low"), "raw low")
        raw_close = _finite_number(source.get("close"), "raw close")
        adjusted_close = _finite_number(
            source.get("adjusted_close"), "adjusted close"
        )
        volume = _finite_number(source.get("volume"), "volume")
        if (
            min(raw_open, raw_high, raw_low, raw_close) <= 0
            or raw_low > min(raw_open, raw_close)
            or raw_high < max(raw_open, raw_close)
            or raw_low > raw_high
        ):
            raise ValueError(f"invalid raw OHLC: {date_text}")
        if adjusted_close <= 0 or volume < 0:
            raise ValueError(f"invalid adjusted price or volume: {date_text}")
        adjustment_factor = adjusted_close / raw_close
        if not math.isfinite(adjustment_factor) or adjustment_factor <= 0:
            raise ValueError(f"invalid adjustment factor: {date_text}")

        break_before_days = None
        if previous_date is not None:
            break_before_days = (row_date - previous_date).days
            if break_before_days > int(gap_days):
                segments.append(
                    _segment_summary(
                        segment_id,
                        segment_rows,
                        is_current_segment=False,
                    )
                )
                segment_id += 1
                segment_rows = []

        row = {
            "date": date_text,
            "raw_open": raw_open,
            "raw_high": raw_high,
            "raw_low": raw_low,
            "raw_close": raw_close,
            "adjusted_open": raw_open * adjustment_factor,
            "adjusted_high": raw_high * adjustment_factor,
            "adjusted_low": raw_low * adjustment_factor,
            "adjusted_close": adjusted_close,
            "adjustment_factor": adjustment_factor,
            "volume": int(volume) if volume.is_integer() else volume,
            "segment_id": segment_id,
        }
        normalized.append(row)
        segment_rows.append((date_text, break_before_days))
        previous_date = row_date

    if segment_rows:
        segments.append(
            _segment_summary(
                segment_id,
                segment_rows,
                is_current_segment=True,
            )
        )
    return normalized, segments


def _is_all_zero_placeholder(row):
    try:
        return all(
            float(row.get(field)) == 0
            for field in (
                "open",
                "high",
                "low",
                "close",
                "adjusted_close",
                "volume",
            )
        )
    except (TypeError, ValueError):
        return False


def _segment_summary(segment_id, rows, *, is_current_segment):
    first_break = rows[0][1]
    return {
        "segment_id": segment_id,
        "first_date": rows[0][0],
        "last_date": rows[-1][0],
        "row_count": len(rows),
        "break_before_days": first_break,
        "is_current_segment": bool(is_current_segment),
    }


class ResearchPriceStore:
    def __init__(self, connection):
        self.connection = connection
        self.connection.execute("PRAGMA foreign_keys = ON")

    def initialize(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS security_master (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                exchange TEXT,
                isin TEXT,
                cik INTEGER,
                security_type TEXT NOT NULL DEFAULT 'Common Stock',
                active INTEGER NOT NULL DEFAULT 1,
                observed_at TEXT NOT NULL,
                provider TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS universe_memberships (
                universe_key TEXT NOT NULL,
                ticker TEXT NOT NULL REFERENCES security_master(ticker),
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                selection_rule TEXT NOT NULL,
                close REAL,
                market_cap REAL,
                avg_volume_50d REAL,
                avg_dollar_volume_50d REAL,
                PRIMARY KEY (universe_key, ticker, effective_from)
            );
            CREATE TABLE IF NOT EXISTS daily_prices (
                ticker TEXT NOT NULL REFERENCES security_master(ticker),
                date TEXT NOT NULL,
                raw_open REAL NOT NULL,
                raw_high REAL NOT NULL,
                raw_low REAL NOT NULL,
                raw_close REAL NOT NULL,
                adjusted_open REAL NOT NULL,
                adjusted_high REAL NOT NULL,
                adjusted_low REAL NOT NULL,
                adjusted_close REAL NOT NULL,
                adjustment_factor REAL NOT NULL,
                volume REAL NOT NULL,
                segment_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                adjustment_method TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_prices_date
                ON daily_prices(date);
            CREATE INDEX IF NOT EXISTS idx_daily_prices_segment
                ON daily_prices(ticker, segment_id, date);
            CREATE TABLE IF NOT EXISTS history_segments (
                ticker TEXT NOT NULL REFERENCES security_master(ticker),
                segment_id INTEGER NOT NULL,
                first_date TEXT NOT NULL,
                last_date TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                break_before_days INTEGER,
                is_current_segment INTEGER NOT NULL,
                PRIMARY KEY (ticker, segment_id)
            );
            CREATE TABLE IF NOT EXISTS splits (
                ticker TEXT NOT NULL REFERENCES security_master(ticker),
                date TEXT NOT NULL,
                ratio_text TEXT NOT NULL,
                numerator REAL NOT NULL,
                denominator REAL NOT NULL,
                provider TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                PRIMARY KEY (ticker, date, ratio_text)
            );
            CREATE TABLE IF NOT EXISTS dividends (
                ticker TEXT NOT NULL REFERENCES security_master(ticker),
                ex_date TEXT NOT NULL,
                declaration_date TEXT,
                record_date TEXT,
                payment_date TEXT,
                period TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL,
                unadjusted_value REAL,
                currency TEXT,
                provider TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                PRIMARY KEY (ticker, ex_date, period, value)
            );
            CREATE TABLE IF NOT EXISTS sector_classifications (
                ticker TEXT NOT NULL REFERENCES security_master(ticker),
                taxonomy TEXT NOT NULL,
                sector_key TEXT NOT NULL,
                benchmark_ticker TEXT,
                industry_code TEXT,
                industry_label TEXT,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                asof TEXT NOT NULL,
                residual_correlation REAL,
                residual_beta REAL,
                relative_return_63d REAL,
                common_days INTEGER,
                agrees_with_sec INTEGER,
                conflict_reason TEXT,
                PRIMARY KEY (ticker, taxonomy, rule_version, asof)
            );
            CREATE TABLE IF NOT EXISTS import_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_version TEXT NOT NULL,
                universe_key TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                security_count INTEGER NOT NULL,
                daily_row_count INTEGER NOT NULL,
                error_count INTEGER NOT NULL,
                errors_json TEXT NOT NULL
            );
            """
        )

    def import_security(
        self,
        security,
        daily_rows,
        split_rows,
        dividend_rows,
        *,
        snapshot_date,
        imported_at,
        provider="eodhd",
        security_type="Common Stock",
        include_membership=True,
    ):
        ticker = str(security.get("ticker") or "").strip().upper()
        if not ticker:
            raise ValueError("missing ticker")
        normalized, segments = normalize_daily_rows(daily_rows)
        splits = [_normalize_split(row) for row in split_rows]
        dividends = [_normalize_dividend(row) for row in dividend_rows]
        classification = security.get("classification") or {}
        asof = str(security.get("asof") or snapshot_date)
        universe_key = str(
            security.get("selection_rule") or "reference_assets_v1"
        )

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO security_master
                    (ticker, name, exchange, isin, cik, security_type, active,
                     observed_at, provider)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, exchange=excluded.exchange,
                    isin=excluded.isin, cik=excluded.cik,
                    security_type=excluded.security_type,
                    active=excluded.active, observed_at=excluded.observed_at,
                    provider=excluded.provider
                """,
                (
                    ticker,
                    str(security.get("name") or ticker),
                    security.get("exchange"),
                    security.get("isin"),
                    security.get("cik"),
                    security_type,
                    asof,
                    provider,
                ),
            )
            for table in (
                "daily_prices",
                "history_segments",
                "splits",
                "dividends",
            ):
                self.connection.execute(
                    f"DELETE FROM {table} WHERE ticker = ?", (ticker,)
                )
            if include_membership:
                self.connection.execute(
                    """
                    INSERT INTO universe_memberships
                        (universe_key, ticker, effective_from, effective_to,
                         selection_rule, close, market_cap, avg_volume_50d,
                         avg_dollar_volume_50d)
                    VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
                    ON CONFLICT(universe_key, ticker, effective_from) DO UPDATE SET
                        effective_to=NULL, selection_rule=excluded.selection_rule,
                        close=excluded.close, market_cap=excluded.market_cap,
                        avg_volume_50d=excluded.avg_volume_50d,
                        avg_dollar_volume_50d=excluded.avg_dollar_volume_50d
                    """,
                    (
                        universe_key,
                        ticker,
                        asof,
                        universe_key,
                        security.get("close"),
                        security.get("market_cap"),
                        security.get("avg_volume_50d"),
                        security.get("avg_dollar_volume_50d"),
                    ),
                )
            if classification.get("sector_key"):
                self.connection.execute(
                    """
                    INSERT INTO sector_classifications
                        (ticker, taxonomy, sector_key, industry_code,
                         industry_label, confidence, source, rule_version, asof)
                    VALUES (?, 'sec', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, taxonomy, rule_version, asof)
                    DO UPDATE SET sector_key=excluded.sector_key,
                        industry_code=excluded.industry_code,
                        industry_label=excluded.industry_label,
                        confidence=excluded.confidence,
                        source=excluded.source
                    """,
                    (
                        ticker,
                        classification.get("sector_key"),
                        classification.get("sic"),
                        classification.get("industry_description"),
                        float(classification.get("confidence") or 0),
                        classification.get("source") or "sec",
                        classification.get("rule_version") or "sec_sic_v1",
                        asof,
                    ),
                )
            self.connection.executemany(
                """
                INSERT INTO daily_prices
                    (ticker, date, raw_open, raw_high, raw_low, raw_close,
                     adjusted_open, adjusted_high, adjusted_low, adjusted_close,
                     adjustment_factor, volume, segment_id, provider,
                     snapshot_date, imported_at, adjustment_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ticker,
                        row["date"],
                        row["raw_open"],
                        row["raw_high"],
                        row["raw_low"],
                        row["raw_close"],
                        row["adjusted_open"],
                        row["adjusted_high"],
                        row["adjusted_low"],
                        row["adjusted_close"],
                        row["adjustment_factor"],
                        row["volume"],
                        row["segment_id"],
                        provider,
                        snapshot_date,
                        imported_at,
                        ADJUSTMENT_METHOD,
                    )
                    for row in normalized
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO history_segments
                    (ticker, segment_id, first_date, last_date, row_count,
                     break_before_days, is_current_segment)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ticker,
                        segment["segment_id"],
                        segment["first_date"],
                        segment["last_date"],
                        segment["row_count"],
                        segment["break_before_days"],
                        int(segment["is_current_segment"]),
                    )
                    for segment in segments
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO splits
                    (ticker, date, ratio_text, numerator, denominator,
                     provider, snapshot_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ticker,
                        row["date"],
                        row["ratio_text"],
                        row["numerator"],
                        row["denominator"],
                        provider,
                        snapshot_date,
                    )
                    for row in splits
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO dividends
                    (ticker, ex_date, declaration_date, record_date,
                     payment_date, period, value, unadjusted_value, currency,
                     provider, snapshot_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ticker,
                        row["ex_date"],
                        row["declaration_date"],
                        row["record_date"],
                        row["payment_date"],
                        row["period"],
                        row["value"],
                        row["unadjusted_value"],
                        row["currency"],
                        provider,
                        snapshot_date,
                    )
                    for row in dividends
                ],
            )
        return ImportSummary(
            ticker=ticker,
            daily_rows=len(normalized),
            segment_count=len(segments),
            split_rows=len(splits),
            dividend_rows=len(dividends),
        )


def _normalize_split(row):
    date_text = date.fromisoformat(str(row.get("date"))).isoformat()
    ratio_text = str(row.get("split") or "")
    try:
        numerator_text, denominator_text = ratio_text.split("/", 1)
        numerator = _finite_number(numerator_text, "split numerator")
        denominator = _finite_number(denominator_text, "split denominator")
    except ValueError as exc:
        raise ValueError(f"invalid split ratio: {ratio_text}") from exc
    if numerator <= 0 or denominator <= 0:
        raise ValueError(f"invalid split ratio: {ratio_text}")
    return {
        "date": date_text,
        "ratio_text": ratio_text,
        "numerator": numerator,
        "denominator": denominator,
    }


def _normalize_optional_date(value):
    if value in (None, "", "0000-00-00"):
        return None
    return date.fromisoformat(str(value)).isoformat()


def _normalize_dividend(row):
    return {
        "ex_date": date.fromisoformat(str(row.get("date"))).isoformat(),
        "declaration_date": _normalize_optional_date(row.get("declarationDate")),
        "record_date": _normalize_optional_date(row.get("recordDate")),
        "payment_date": _normalize_optional_date(row.get("paymentDate")),
        "period": str(row.get("period") or ""),
        "value": _finite_number(row.get("value"), "dividend value"),
        "unadjusted_value": (
            None
            if row.get("unadjustedValue") is None
            else _finite_number(row.get("unadjustedValue"), "unadjusted dividend")
        ),
        "currency": row.get("currency"),
    }


def load_json_list(path):
    path = Path(path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError(f"expected JSON list: {path}")
    return payload
