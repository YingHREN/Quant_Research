from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from web.services.research_classification import ResearchClassificationService


def create_classification_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE security_master (
            ticker TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE universe_memberships (
            universe_key TEXT NOT NULL,
            ticker TEXT NOT NULL,
            effective_from TEXT NOT NULL
        );
        CREATE TABLE sector_classifications (
            ticker TEXT NOT NULL,
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
            conflict_reason TEXT
        );
        CREATE TABLE group_assignments (
            ticker TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            observed_at TEXT NOT NULL,
            sector_key TEXT NOT NULL,
            sector_benchmark TEXT,
            theme_keys_json TEXT NOT NULL,
            theme_benchmarks_json TEXT NOT NULL,
            primary_model_group TEXT NOT NULL,
            classification_state TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL NOT NULL,
            override_reason TEXT,
            PRIMARY KEY (ticker, rule_version, effective_from)
        );
        """
    )
    connection.executemany(
        "INSERT INTO security_master(ticker, name) VALUES (?, ?)",
        [("AAA", "Agree"), ("BBB", "Conflict"), ("CCC", "SEC only")],
    )
    connection.executemany(
        """
        INSERT INTO universe_memberships(universe_key, ticker, effective_from)
        VALUES ('liquid_us_common_v1', ?, '2026-07-24')
        """,
        [("AAA",), ("BBB",), ("CCC",)],
    )
    rows = [
        (
            "AAA",
            "sec",
            "technology",
            None,
            "7372",
            "Software",
            1.0,
            "sec",
            "sec_sic_v1",
            "2026-07-24",
            None,
            None,
            None,
            None,
            None,
            None,
        ),
        (
            "AAA",
            "market_behavior",
            "technology",
            "XLK",
            None,
            None,
            0.82,
            "price_returns",
            "market_behavior_v1",
            "2026-07-24",
            0.42,
            1.25,
            0.08,
            252,
            1,
            "与 SEC 基本面板块一致",
        ),
        (
            "BBB",
            "sec",
            "technology",
            None,
            "7372",
            "Software",
            1.0,
            "sec",
            "sec_sic_v1",
            "2026-07-24",
            None,
            None,
            None,
            None,
            None,
            None,
        ),
        (
            "BBB",
            "market_behavior",
            "financials",
            "XLF",
            None,
            None,
            0.55,
            "price_returns",
            "market_behavior_v1",
            "2026-07-24",
            0.30,
            0.65,
            -0.12,
            200,
            0,
            "SEC 基本面板块为 technology，价格行为更接近 financials（XLF）",
        ),
        (
            "CCC",
            "sec",
            "technology",
            None,
            "7372",
            "Software",
            0.8,
            "sec",
            "sec_sic_v1",
            "2026-07-24",
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    ]
    connection.executemany(
        """
        INSERT INTO sector_classifications
            (ticker, taxonomy, sector_key, benchmark_ticker, industry_code,
             industry_label, confidence, source, rule_version, asof,
             residual_correlation, residual_beta, relative_return_63d,
             common_days, agrees_with_sec, conflict_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.executemany(
        """
        INSERT INTO group_assignments
            (ticker, rule_version, effective_from, effective_to, observed_at,
             sector_key, sector_benchmark, theme_keys_json,
             theme_benchmarks_json, primary_model_group,
             classification_state, source, confidence, override_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "AAA", "group_assignment_v1", "2026-01-01", None,
                "2026-07-24", "technology", "XLK", '[]', '{}',
                "technology", "classified", "sec_broad", 1.0, None,
            ),
            (
                "BBB", "group_assignment_v1", "2026-01-01", None,
                "2026-07-24", "financials", "XLF", '[]', '{}',
                "financials", "classified", "market_behavior", 0.55, None,
            ),
            (
                "CCC", "group_assignment_v1", "2026-01-01", None,
                "2026-07-24", "unclassified_review", None, '[]', '{}',
                "unclassified_review", "needs_review", "review", 0.0, None,
            ),
        ],
    )
    connection.commit()
    connection.close()


class ResearchClassificationServiceTest(unittest.TestCase):
    def test_build_returns_states_metadata_and_full_research_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            create_classification_database(database)
            service = ResearchClassificationService(database)

            payload = service.build(["AAA", "BBB", "CCC", "MISSING"])

        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["asof"], "2026-07-24")
        self.assertEqual(payload["research_universe_count"], 3)
        self.assertEqual(payload["group_assignment_coverage"], 0.75)
        self.assertEqual(payload["group_assignment_review_count"], 1)
        self.assertEqual(payload["sector_counts"]["sec"], {"technology": 3})
        self.assertEqual(
            payload["sector_counts"]["market_behavior"],
            {"financials": 1, "technology": 1, "unclassified": 1},
        )
        self.assertEqual(payload["by_ticker"]["AAA"]["state"], "agree")
        self.assertEqual(payload["by_ticker"]["BBB"]["state"], "conflict")
        self.assertEqual(payload["by_ticker"]["CCC"]["state"], "sec_only")
        self.assertEqual(
            payload["by_ticker"]["MISSING"]["state"],
            "unclassified",
        )
        behavior = payload["by_ticker"]["BBB"]["market_behavior"]
        self.assertEqual(behavior["benchmark_ticker"], "XLF")
        self.assertEqual(behavior["common_days"], 200)
        self.assertAlmostEqual(behavior["confidence"], 0.55)
        self.assertIn("technology", behavior["conflict_reason"])
        self.assertEqual(
            payload["by_ticker"]["BBB"]["group_assignment"]
            ["primary_model_group"],
            "financials",
        )
        self.assertEqual(
            payload["by_ticker"]["MISSING"]["group_assignment"],
            {
                "state": "missing",
                "reason": "no_assignment_effective_at_asof",
            },
        )

    def test_missing_database_degrades_without_hiding_requested_tickers(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ResearchClassificationService(
                Path(directory) / "missing.db"
            )

            payload = service.build(["AAA", "BBB"])

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["research_universe_count"], 0)
        self.assertEqual(payload["sector_counts"], {})
        self.assertEqual(payload["group_assignment_coverage"], 0.0)
        self.assertEqual(
            set(payload["by_ticker"]),
            {"AAA", "BBB"},
        )
        self.assertTrue(
            all(
                row["state"] == "unclassified"
                for row in payload["by_ticker"].values()
            )
        )


if __name__ == "__main__":
    unittest.main()
