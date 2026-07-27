"""Read-only research-universe sector classification metadata."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import sqlite3
from threading import RLock

from web.services.group_assignments import GroupAssignmentRepository


TAXONOMIES = ("sec", "market_behavior")


def _unclassified():
    return {
        "state": "unclassified",
        "sec": None,
        "market_behavior": None,
    }


class ResearchClassificationService:
    """Read small metadata tables without touching research price history."""

    def __init__(self, database_path, max_cache_size=2):
        if isinstance(max_cache_size, bool) or not isinstance(max_cache_size, int):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self.database_path = Path(database_path)
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._lock = RLock()
        self._group_assignments = GroupAssignmentRepository(
            self.database_path,
            max_cache_size=max_cache_size,
        )

    def build(self, tickers, asof=None):
        normalized = tuple(
            sorted(
                {
                    str(ticker).strip().upper()
                    for ticker in tickers
                    if str(ticker).strip()
                }
            )
        )
        try:
            revision = self.database_path.stat().st_mtime_ns
        except OSError:
            payload = _unavailable_payload(normalized)
            _merge_assignments(
                payload,
                self._group_assignments.build(normalized, asof=asof),
            )
            return payload
        key = (revision, normalized, asof)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)
        try:
            payload = self._read(normalized)
        except (OSError, sqlite3.Error, ValueError):
            payload = _unavailable_payload(normalized)
        assignment_payload = self._group_assignments.build(normalized, asof=asof)
        _merge_assignments(payload, assignment_payload)
        with self._lock:
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        return deepcopy(payload)

    def _read(self, tickers):
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            universe_count = connection.execute(
                "SELECT COUNT(DISTINCT ticker) FROM universe_memberships"
            ).fetchone()[0]
            asof = connection.execute(
                "SELECT MAX(effective_from) FROM universe_memberships"
            ).fetchone()[0]
            sector_counts = _sector_counts(connection, universe_count)
            classifications = _classification_rows(connection, tickers)
        finally:
            connection.close()

        by_ticker = {ticker: _unclassified() for ticker in tickers}
        for row in classifications:
            ticker = row["ticker"]
            taxonomy = row["taxonomy"]
            if taxonomy not in TAXONOMIES:
                continue
            by_ticker[ticker][taxonomy] = _classification_dict(row)
        for classification in by_ticker.values():
            classification["state"] = _classification_state(classification)
        return {
            "status": "available",
            "asof": asof,
            "research_universe_count": int(universe_count),
            "sector_counts": sector_counts,
            "by_ticker": by_ticker,
        }


def _classification_rows(connection, tickers):
    rows = []
    for offset in range(0, len(tickers), 900):
        chunk = tickers[offset : offset + 900]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            connection.execute(
                f"""
                SELECT ticker, taxonomy, sector_key, benchmark_ticker,
                       industry_code, industry_label, confidence, source,
                       rule_version, asof, residual_correlation, residual_beta,
                       relative_return_63d, common_days, agrees_with_sec,
                       conflict_reason
                FROM sector_classifications
                WHERE ticker IN ({placeholders})
                  AND taxonomy IN ('sec', 'market_behavior')
                ORDER BY ticker, taxonomy
                """,
                chunk,
            ).fetchall()
        )
    return rows


def _sector_counts(connection, universe_count):
    result = {}
    for taxonomy in TAXONOMIES:
        counts = {
            str(row["sector_key"]): int(row["member_count"])
            for row in connection.execute(
                """
                SELECT sector_key, COUNT(DISTINCT ticker) AS member_count
                FROM sector_classifications
                WHERE taxonomy = ?
                  AND ticker IN (SELECT ticker FROM universe_memberships)
                GROUP BY sector_key
                ORDER BY sector_key
                """,
                (taxonomy,),
            ).fetchall()
        }
        classified = sum(counts.values())
        if classified < universe_count:
            counts["unclassified"] = int(universe_count - classified)
        result[taxonomy] = counts
    return result


def _classification_dict(row):
    return {
        "sector_key": row["sector_key"],
        "benchmark_ticker": row["benchmark_ticker"],
        "industry_code": row["industry_code"],
        "industry_label": row["industry_label"],
        "confidence": row["confidence"],
        "source": row["source"],
        "rule_version": row["rule_version"],
        "asof": row["asof"],
        "residual_correlation": row["residual_correlation"],
        "residual_beta": row["residual_beta"],
        "relative_return_63d": row["relative_return_63d"],
        "common_days": row["common_days"],
        "agrees_with_sec": (
            None
            if row["agrees_with_sec"] is None
            else bool(row["agrees_with_sec"])
        ),
        "conflict_reason": row["conflict_reason"],
    }


def _classification_state(classification):
    sec = classification.get("sec")
    behavior = classification.get("market_behavior")
    if sec and behavior:
        return (
            "agree"
            if sec.get("sector_key") == behavior.get("sector_key")
            else "conflict"
        )
    if sec:
        return "sec_only"
    if behavior:
        return "behavior_only"
    return "unclassified"


def _unavailable_payload(tickers):
    return {
        "status": "unavailable",
        "asof": None,
        "research_universe_count": 0,
        "sector_counts": {},
        "by_ticker": {ticker: _unclassified() for ticker in tickers},
    }


def _merge_assignments(payload, assignments):
    payload["group_assignment_status"] = assignments["status"]
    payload["group_assignment_asof"] = assignments["asof"]
    payload["group_assignment_revision"] = assignments["revision"]
    payload["group_assignment_coverage"] = assignments["coverage"]
    payload["group_assignment_review_count"] = assignments["review_count"]
    for ticker, classification in payload["by_ticker"].items():
        classification["group_assignment"] = assignments["by_ticker"][ticker]
