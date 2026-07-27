"""Isolated SQLite store for delisted identity and industry evidence."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3


class DelistedReferenceStore:
    """Persist reference evidence without touching any price database."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        self.connection.close()

    def _create_schema(self):
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS coverage_sample (
                ticker TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                name TEXT NOT NULL,
                provider_isin TEXT,
                identity_panel TEXT NOT NULL,
                selection_hash TEXT NOT NULL,
                sample_version TEXT NOT NULL,
                valid_rows INTEGER NOT NULL,
                last_date TEXT,
                catalog_sha256 TEXT NOT NULL,
                snapshot_date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_classification_snapshots (
                ticker TEXT NOT NULL REFERENCES coverage_sample(ticker),
                sector TEXT,
                industry TEXT,
                snapshot_at TEXT NOT NULL,
                historical_eligibility TEXT NOT NULL
                    CHECK (historical_eligibility = 'snapshot_only'),
                source TEXT NOT NULL,
                PRIMARY KEY (ticker, source, snapshot_at)
            );
            CREATE TABLE IF NOT EXISTS identity_evidence (
                ticker TEXT NOT NULL REFERENCES coverage_sample(ticker),
                key_type TEXT NOT NULL,
                key_value TEXT NOT NULL,
                source TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                available_at TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence_status TEXT NOT NULL,
                raw_sha256 TEXT,
                reason_codes_json TEXT NOT NULL,
                PRIMARY KEY (
                    ticker, source, source_record_id, key_type, key_value
                )
            );
            CREATE TABLE IF NOT EXISTS security_entity_links (
                ticker TEXT PRIMARY KEY REFERENCES coverage_sample(ticker),
                cik TEXT,
                link_status TEXT NOT NULL CHECK (
                    link_status IN (
                        'confirmed', 'review_required',
                        'rejected', 'unresolved'
                    )
                ),
                decision_rule TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                supporting_evidence_json TEXT NOT NULL,
                conflicting_evidence_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sec_industry_observations (
                cik TEXT NOT NULL,
                sic TEXT NOT NULL,
                industry_label TEXT,
                accession_number TEXT PRIMARY KEY,
                filing_date TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                source TEXT NOT NULL,
                raw_sha256 TEXT,
                parser_version TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sec_industry_intervals (
                cik TEXT NOT NULL,
                sic TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                first_accession TEXT NOT NULL,
                last_supporting_accession TEXT NOT NULL,
                observation_count INTEGER NOT NULL,
                taxonomy_version TEXT NOT NULL,
                interval_rule_version TEXT NOT NULL,
                PRIMARY KEY (cik, valid_from),
                CHECK (valid_to IS NULL OR valid_to > valid_from)
            );
            CREATE TABLE IF NOT EXISTS market_behavior_classifications (
                ticker TEXT NOT NULL REFERENCES coverage_sample(ticker),
                asof TEXT NOT NULL,
                sector_key TEXT,
                benchmark_ticker TEXT,
                common_days INTEGER NOT NULL,
                residual_correlation REAL,
                residual_beta REAL,
                relative_return REAL,
                confidence REAL NOT NULL,
                coverage_status TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                PRIMARY KEY (ticker, asof, rule_version)
            );
            CREATE TABLE IF NOT EXISTS identity_conflicts (
                conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL REFERENCES coverage_sample(ticker),
                reason_code TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rejected_industry_observations (
                rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cik TEXT,
                accession_number TEXT,
                reason_code TEXT NOT NULL,
                raw_sha256 TEXT,
                evidence_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                sample_version TEXT NOT NULL,
                catalog_sha256 TEXT NOT NULL,
                summary_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_artifacts (
                artifact_name TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (artifact_name, snapshot_date, sha256)
            );
            """
        )

    def replace_sample(self, sample, catalog_sha256, snapshot_date):
        rows = tuple(sample)
        if not rows:
            raise ValueError("coverage sample must not be empty")
        snapshot_date = date.fromisoformat(
            str(snapshot_date)
        ).isoformat()
        with self.connection:
            for table in (
                "market_behavior_classifications",
                "identity_conflicts",
                "identity_evidence",
                "security_entity_links",
                "provider_classification_snapshots",
            ):
                self.connection.execute(f"DELETE FROM {table}")
            self.connection.execute("DELETE FROM coverage_sample")
            self.connection.executemany(
                """
                INSERT INTO coverage_sample
                    (ticker, exchange, name, provider_isin, identity_panel,
                     selection_hash, sample_version, valid_rows, last_date,
                     catalog_sha256, snapshot_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row["ticker"]),
                        str(row["exchange"]),
                        str(row["name"]),
                        row.get("provider_isin"),
                        str(row["identity_panel"]),
                        str(row["selection_hash"]),
                        str(row["sample_version"]),
                        int(row.get("valid_rows") or 0),
                        row.get("last_date"),
                        str(catalog_sha256),
                        snapshot_date,
                    )
                    for row in rows
                ],
            )

    def replace_provider_snapshots(self, snapshots):
        rows = tuple(snapshots)
        if not rows:
            raise ValueError("provider snapshots must not be empty")
        with self.connection:
            self.connection.execute(
                "DELETE FROM provider_classification_snapshots"
            )
            self.connection.executemany(
                """
                INSERT INTO provider_classification_snapshots
                    (ticker, sector, industry, snapshot_at,
                     historical_eligibility, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row["ticker"]),
                        row.get("sector"),
                        row.get("industry"),
                        str(row["snapshot_at"]),
                        str(row["historical_eligibility"]),
                        str(row["source"]),
                    )
                    for row in rows
                ],
            )

    def replace_identity_results(self, results, evidence):
        results = tuple(results)
        evidence = tuple(evidence)
        if not results:
            raise ValueError("identity results must not be empty")
        with self.connection:
            self.connection.execute("DELETE FROM identity_evidence")
            self.connection.execute("DELETE FROM security_entity_links")
            self.connection.executemany(
                """
                INSERT INTO security_entity_links
                    (ticker, cik, link_status, decision_rule, rule_version,
                     reason_codes_json, supporting_evidence_json,
                     conflicting_evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row["ticker"]).upper(),
                        row.get("cik"),
                        str(row["link_status"]),
                        str(row["decision_rule"]),
                        str(row["rule_version"]),
                        _json(row.get("reason_codes") or []),
                        _json(row.get("supporting_evidence") or []),
                        _json(row.get("conflicting_evidence") or []),
                    )
                    for row in results
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO identity_evidence
                    (ticker, key_type, key_value, source, source_record_id,
                     available_at, confidence, evidence_status, raw_sha256,
                     reason_codes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row["ticker"]).upper(),
                        str(row["key_type"]),
                        str(row["key_value"]),
                        str(row["source"]),
                        str(row["source_record_id"]),
                        str(row["available_at"]),
                        float(row.get("confidence") or 0),
                        str(row.get("evidence_status") or "observed"),
                        row.get("raw_sha256"),
                        _json(row.get("reason_codes") or []),
                    )
                    for row in evidence
                ],
            )

    def replace_sic_observations(self, observations, intervals):
        observations = tuple(observations)
        intervals = tuple(intervals)
        if not observations or not intervals:
            raise ValueError("SIC observations and intervals must not be empty")
        with self.connection:
            self.connection.execute("DELETE FROM sec_industry_intervals")
            self.connection.execute("DELETE FROM sec_industry_observations")
            self.connection.executemany(
                """
                INSERT INTO sec_industry_observations
                    (cik, sic, industry_label, accession_number, filing_date,
                     accepted_at, available_at, source, raw_sha256,
                     parser_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row["cik"]),
                        str(row["sic"]),
                        row.get("industry_label"),
                        str(row["accession_number"]),
                        str(row["filing_date"]),
                        str(row["accepted_at"]),
                        str(row["available_at"]),
                        str(row["source"]),
                        row.get("raw_sha256"),
                        str(row["parser_version"]),
                    )
                    for row in observations
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO sec_industry_intervals
                    (cik, sic, valid_from, valid_to, first_accession,
                     last_supporting_accession, observation_count,
                     taxonomy_version, interval_rule_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(row["cik"]),
                        str(row["sic"]),
                        str(row["valid_from"]),
                        row.get("valid_to"),
                        str(row["first_accession"]),
                        str(row["last_supporting_accession"]),
                        int(row["observation_count"]),
                        str(row["taxonomy_version"]),
                        str(row["interval_rule_version"]),
                    )
                    for row in intervals
                ],
            )

    def classification_asof(self, ticker, asof):
        row = self.connection.execute(
            """
            SELECT i.*
            FROM security_entity_links AS l
            JOIN sec_industry_intervals AS i ON i.cik = l.cik
            WHERE l.ticker = ?
              AND l.link_status = 'confirmed'
              AND i.valid_from <= ?
              AND (i.valid_to IS NULL OR ? < i.valid_to)
            ORDER BY i.valid_from DESC
            LIMIT 1
            """,
            (str(ticker).upper(), str(asof), str(asof)),
        ).fetchone()
        return None if row is None else dict(row)

    def integrity_report(self):
        integrity = self.connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        foreign_keys = len(
            self.connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        return {
            "integrity_check": integrity,
            "foreign_key_errors": foreign_keys,
        }


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
