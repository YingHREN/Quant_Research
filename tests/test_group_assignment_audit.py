from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from build_research_db import build_database
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
