from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from audit_group_assignments import audit_database, strict_failure
from build_research_db import build_database
from data.market_behavior import MarketBehaviorResult
from data.research_store import ResearchPriceStore
from web.market_groups import REFERENCE_TICKERS


def _daily_row():
    return [
        {
            "date": "2026-07-24",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "adjusted_close": 10,
            "volume": 1000,
        }
    ]


def _initialize_audit_database(path):
    connection = sqlite3.connect(path)
    ResearchPriceStore(connection).initialize()
    connection.executemany(
        """
        INSERT INTO security_master
            (ticker, name, security_type, active, observed_at, provider)
        VALUES (?, ?, 'Common Stock', 1, '2026-07-24', 'fixture')
        """,
        [
            ("CHIP", "Chip Fixture"),
            ("REVIEW", "Review Fixture"),
            ("MISSING", "Missing Fixture"),
        ],
    )
    connection.executemany(
        """
        INSERT INTO group_assignments
            (ticker, rule_version, effective_from, effective_to, observed_at,
             sector_key, sector_benchmark, theme_keys_json,
             theme_benchmarks_json, primary_model_group,
             classification_state, source, confidence, override_reason)
        VALUES (?, ?, ?, ?, '2026-07-24', ?, ?, ?, ?, ?, ?, 'fixture', 1.0, NULL)
        """,
        [
            (
                "CHIP",
                "fixture_v1",
                "2026-01-01",
                None,
                "technology",
                "XLK",
                '["semiconductor"]',
                '{"semiconductor":["SOXX","SMH"]}',
                "semiconductor",
                "classified",
            ),
            (
                "REVIEW",
                "fixture_v1",
                "2026-01-01",
                None,
                "unclassified_review",
                None,
                "[]",
                "{}",
                "unclassified_review",
                "needs_review",
            ),
        ],
    )
    connection.commit()
    connection.close()


class GroupAssignmentAuditCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "research.sqlite"
        _initialize_audit_database(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_audit_distinguishes_explicit_review_from_missing_assignment(self):
        result = audit_database(self.database, asof="2026-07-24")

        self.assertEqual(result["active_common_stocks"], 3)
        self.assertEqual(result["assigned"], 2)
        self.assertEqual(result["coverage"], 2 / 3)
        self.assertEqual(
            result["needs_review"],
            {"count": 1, "tickers": ["REVIEW"]},
        )
        self.assertEqual(
            result["missing"],
            {"count": 1, "tickers": ["MISSING"]},
        )
        self.assertEqual(result["invalid_benchmarks"], [])
        self.assertEqual(result["theme_counts"], {"semiconductor": 1})
        self.assertEqual(result["conflicts"], [])

    def test_audit_reports_complete_coverage(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            INSERT INTO group_assignments
                (ticker, rule_version, effective_from, effective_to, observed_at,
                 sector_key, sector_benchmark, theme_keys_json,
                 theme_benchmarks_json, primary_model_group,
                 classification_state, source, confidence, override_reason)
            VALUES
                ('MISSING', 'fixture_v1', '2026-01-01', NULL, '2026-07-24',
                 'financials', 'XLF', '[]', '{}', 'financials',
                 'classified', 'fixture', 1.0, NULL)
            """
        )
        connection.commit()
        connection.close()

        result = audit_database(self.database, asof="2026-07-24")

        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["invalid_benchmarks"], [])
        self.assertIn("semiconductor", result["theme_counts"])
        self.assertEqual(result["missing"], {"count": 0, "tickers": []})

    def test_audit_reports_invalid_sector_and_theme_references(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            UPDATE group_assignments
            SET sector_benchmark = 'XLF',
                theme_benchmarks_json = '{"semiconductor":["BAD"]}'
            WHERE ticker = 'CHIP'
            """
        )
        connection.commit()
        connection.close()

        result = audit_database(self.database, asof="2026-07-24")

        self.assertEqual(
            result["invalid_benchmarks"],
            [
                {
                    "actual": ["BAD"],
                    "expected": ["SOXX", "SMH"],
                    "group": "semiconductor",
                    "kind": "theme",
                    "ticker": "CHIP",
                },
                {
                    "actual": "XLF",
                    "expected": "XLK",
                    "group": "technology",
                    "kind": "sector",
                    "ticker": "CHIP",
                },
            ],
        )

    def test_audit_rejects_unknown_sector_and_theme_with_null_references(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            UPDATE group_assignments
            SET sector_key = 'bogus_sector',
                sector_benchmark = NULL,
                theme_keys_json = '["mystery_theme"]',
                theme_benchmarks_json = '{"mystery_theme":null}'
            WHERE ticker = 'CHIP'
            """
        )
        connection.commit()
        connection.close()

        result = audit_database(self.database, asof="2026-07-24")

        self.assertEqual(
            result["invalid_benchmarks"],
            [
                {
                    "actual": None,
                    "expected": None,
                    "group": "mystery_theme",
                    "kind": "theme",
                    "reason": "unknown_group",
                    "ticker": "CHIP",
                },
                {
                    "actual": None,
                    "expected": None,
                    "group": "bogus_sector",
                    "kind": "sector",
                    "reason": "unknown_group",
                    "ticker": "CHIP",
                },
            ],
        )
        self.assertTrue(strict_failure(result))

    def test_audit_reports_overlapping_ranges_but_allows_touching_ranges(self):
        connection = sqlite3.connect(self.database)
        connection.executemany(
            """
            INSERT INTO group_assignments
                (ticker, rule_version, effective_from, effective_to, observed_at,
                 sector_key, sector_benchmark, theme_keys_json,
                 theme_benchmarks_json, primary_model_group,
                 classification_state, source, confidence, override_reason)
            VALUES
                ('CHIP', ?, ?, ?, '2026-07-24', 'technology', 'XLK',
                 '["semiconductor"]', '{"semiconductor":["SOXX","SMH"]}',
                 'semiconductor', 'classified', 'fixture', 1.0, NULL)
            """,
            [
                ("fixture_history_v1", "2025-01-01", "2026-01-01"),
                ("fixture_overlap_v1", "2025-12-31", "2026-01-02"),
            ],
        )
        connection.commit()
        connection.close()

        result = audit_database(self.database, asof="2026-07-24")

        self.assertEqual(
            result["conflicts"],
            [
                {
                    "current_effective_from": "2025-12-31",
                    "current_rule_version": "fixture_overlap_v1",
                    "kind": "overlapping_effective_ranges",
                    "previous_effective_to": "2026-01-01",
                    "previous_rule_version": "fixture_history_v1",
                    "ticker": "CHIP",
                },
                {
                    "current_effective_from": "2026-01-01",
                    "current_rule_version": "fixture_v1",
                    "kind": "overlapping_effective_ranges",
                    "previous_effective_to": "2026-01-02",
                    "previous_rule_version": "fixture_overlap_v1",
                    "ticker": "CHIP",
                },
            ],
        )

    def test_strict_cli_prints_deterministic_json_and_exits_nonzero(self):
        command = [
            sys.executable,
            str(Path(__file__).parents[1] / "audit_group_assignments.py"),
            "--database",
            str(self.database),
            "--asof",
            "2026-07-24",
            "--strict",
        ]

        first = subprocess.run(command, capture_output=True, text=True, check=False)
        second = subprocess.run(command, capture_output=True, text=True, check=False)

        self.assertEqual(first.returncode, 1)
        self.assertEqual(first.stderr, "")
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            json.loads(first.stdout)["missing"],
            {"count": 1, "tickers": ["MISSING"]},
        )


class GroupAssignmentPublicationGateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog_path = self.root / "catalog.json"
        self.raw_root = self.root / "2026-07-24"
        self.output_path = self.root / "research.sqlite"
        (self.raw_root / "splits").mkdir(parents=True)
        (self.raw_root / "dividends").mkdir()
        self.catalog_path.write_text(
            json.dumps(
                {
                    "asof": "2026-07-24",
                    "universe_key": "fixture_universe_v1",
                    "securities": [
                        {
                            "ticker": "ZZZZ",
                            "name": "Unresolved Fixture",
                            "exchange": "US",
                            "classification": {
                                "sector_key": "unclassified",
                                "theme_keys": [],
                                "confidence": 0.0,
                                "source": "sec",
                                "rule_version": "sec_sic_v1",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        for ticker in (*REFERENCE_TICKERS, "ZZZZ"):
            (self.raw_root / f"{ticker}.json").write_text(
                json.dumps(_daily_row()), encoding="utf-8"
            )

    def tearDown(self):
        self.temporary.cleanup()

    def build(self):
        return build_database(
            self.catalog_path,
            self.raw_root,
            self.output_path,
            imported_at="2026-07-25T00:00:00Z",
        )

    def replace_catalog_securities(self, securities):
        self.catalog_path.write_text(
            json.dumps(
                {
                    "asof": "2026-07-24",
                    "universe_key": "fixture_universe_v1",
                    "securities": securities,
                }
            ),
            encoding="utf-8",
        )
        for security in securities:
            ticker = security["ticker"]
            (self.raw_root / f"{ticker}.json").write_text(
                json.dumps(_daily_row()), encoding="utf-8"
            )

    def test_build_publishes_common_stock_assignment_coverage_and_review(self):
        result = self.build()

        self.assertEqual(result["group_assignment_count"], 1)
        self.assertEqual(result["group_assignment_review_count"], 1)
        self.assertEqual(result["group_assignment_coverage"], 1.0)
        connection = sqlite3.connect(self.output_path)
        self.assertEqual(
            connection.execute(
                """
                SELECT sector_key, classification_state
                FROM group_assignments
                WHERE ticker = 'ZZZZ'
                """
            ).fetchone(),
            ("unclassified_review", "needs_review"),
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM group_assignments").fetchone()[0],
            1,
        )

    def test_behavior_fallback_persists_only_the_final_assignment(self):
        behavior = MarketBehaviorResult(
            sector_key="financials",
            benchmark_ticker="XLF",
            residual_correlation=0.8,
            residual_beta=1.1,
            relative_return_63d=0.1,
            common_days=252,
            confidence=0.9,
            agrees_with_sec=False,
            conflict_reason="fixture behavior fallback",
            rule_version="market_behavior_v1",
            asof="2026-07-24",
        )

        with patch(
            "build_research_db.classify_market_behavior",
            return_value=behavior,
        ):
            self.build()

        connection = sqlite3.connect(self.output_path)
        self.assertEqual(
            connection.execute(
                """
                SELECT rule_version, sector_key, classification_state
                FROM group_assignments
                WHERE ticker = 'ZZZZ'
                """
            ).fetchall(),
            [("market_behavior_v1", "financials", "classified")],
        )

    def test_build_rejects_missing_standard_reference_etf_before_publication(self):
        with patch("build_research_db.REFERENCE_TICKERS", ("SPY", "QQQ", "XLC")):
            with self.assertRaisesRegex(
                ValueError, "missing standard reference ETF mappings"
            ):
                self.build()

        self.assertFalse(self.output_path.exists())

    def test_failed_assignment_audit_preserves_previous_output(self):
        self.build()
        original = self.output_path.read_bytes()

        with patch(
            "build_research_db.audit_assignments",
            return_value={
                "coverage": 1.0,
                "needs_review_count": 0,
                "invalid_benchmarks": ["BAD"],
                "duplicate_themes": [],
                "conflicting_assignments": [],
            },
        ):
            with self.assertRaisesRegex(ValueError, "group assignment audit failed"):
                self.build()

        self.assertEqual(self.output_path.read_bytes(), original)

    def test_duplicate_catalog_common_stock_ticker_is_rejected_before_publication(self):
        self.build()
        original = self.output_path.read_bytes()
        duplicate = {
            "ticker": "ZZZZ",
            "name": "Duplicate Fixture",
            "exchange": "US",
            "classification": {
                "sector_key": "unclassified",
                "theme_keys": [],
                "confidence": 0.0,
                "source": "sec",
                "rule_version": "sec_sic_v1",
            },
        }
        self.replace_catalog_securities([duplicate, duplicate])

        with self.assertRaisesRegex(
            ValueError, "duplicate catalog common-stock tickers: ZZZZ"
        ):
            self.build()

        self.assertEqual(self.output_path.read_bytes(), original)

    def test_persisted_assignment_cardinality_must_match_catalog(self):
        second = {
            "ticker": "YYYY",
            "name": "Second Fixture",
            "exchange": "US",
            "classification": {
                "sector_key": "unclassified",
                "theme_keys": [],
                "confidence": 0.0,
                "source": "sec",
                "rule_version": "sec_sic_v1",
            },
        }
        self.replace_catalog_securities(
            [
                {
                    "ticker": "ZZZZ",
                    "name": "Unresolved Fixture",
                    "exchange": "US",
                    "classification": {
                        "sector_key": "unclassified",
                        "theme_keys": [],
                        "confidence": 0.0,
                        "source": "sec",
                        "rule_version": "sec_sic_v1",
                    },
                },
                second,
            ]
        )
        original_persist = ResearchPriceStore.persist_group_assignment

        def skip_second_assignment(store, assignment, **kwargs):
            if assignment.ticker == "YYYY":
                return assignment
            return original_persist(store, assignment, **kwargs)

        with patch.object(
            ResearchPriceStore,
            "persist_group_assignment",
            new=skip_second_assignment,
        ):
            with self.assertRaisesRegex(
                ValueError, "persisted group assignment count mismatch"
            ):
                self.build()

        self.assertFalse(self.output_path.exists())


if __name__ == "__main__":
    unittest.main()
