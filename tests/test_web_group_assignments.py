from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from web.services.group_assignments import GroupAssignmentRepository


def create_assignment_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
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
                "SNDK", "group_assignment_v1", "2026-01-01", "2026-07-01",
                "2026-01-01", "technology", "XLK", '["semiconductor"]',
                '{"semiconductor":["SOXX"]}', "technology", "classified",
                "sec_exact", 0.9, None,
            ),
            (
                "SNDK", "group_override_v1", "2026-07-01", None,
                "2026-07-01", "technology", "XLK", '["semiconductor"]',
                '{"semiconductor":["SOXX"]}', "semiconductor", "classified",
                "override", 1.0, "flash storage",
            ),
            (
                "REVIEW", "group_assignment_v1", "2026-01-01", None,
                "2026-01-01", "unclassified_review", None, '[]', '{}',
                "unclassified_review", "needs_review", "review", 0.0, None,
            ),
            (
                "DUP", "group_assignment_v1", "2026-01-01", None,
                "2026-01-01", "technology", "XLK", '[]', '{}', "technology",
                "classified", "sec_broad", 0.8, None,
            ),
            (
                "DUP", "group_override_v1", "2026-01-01", None,
                "2026-01-01", "financials", "XLF", '[]', '{}', "financials",
                "classified", "override", 1.0, "test ambiguity",
            ),
        ],
    )
    connection.commit()
    connection.close()


class GroupAssignmentRepositoryTest(unittest.TestCase):
    def test_repository_selects_assignment_effective_at_asof(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            create_assignment_database(database)
            repository = GroupAssignmentRepository(database)

            before_boundary = repository.build(["SNDK"], asof="2026-06-30")
            at_boundary = repository.build(["SNDK"], asof="2026-07-01")

        self.assertEqual(
            before_boundary["by_ticker"]["SNDK"]["primary_model_group"],
            "technology",
        )
        self.assertEqual(
            at_boundary["by_ticker"]["SNDK"]["primary_model_group"],
            "semiconductor",
        )
        self.assertEqual(
            at_boundary["by_ticker"]["SNDK"]["sector_benchmark"], "XLK"
        )

    def test_repository_reports_same_date_ambiguity_instead_of_selecting_a_row(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            create_assignment_database(database)

            result = GroupAssignmentRepository(database).build(
                ["DUP"], asof="2026-07-01"
            )

        self.assertEqual(result["coverage"], 0.0)
        self.assertEqual(result["by_ticker"]["DUP"], {
            "state": "missing",
            "reason": "ambiguous_assignment_effective_at_asof",
        })

    def test_repository_returns_fresh_json_safe_copies_and_missing_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            create_assignment_database(database)
            repository = GroupAssignmentRepository(database)

            first = repository.build(["SNDK", "MISSING"], asof="2026-07-01")
            first["by_ticker"]["SNDK"]["theme_keys"].append("changed")
            second = repository.build(["SNDK", "MISSING"], asof="2026-07-01")

        self.assertEqual(second["coverage"], 0.5)
        self.assertEqual(
            second["by_ticker"]["SNDK"]["theme_keys"], ["semiconductor"]
        )
        self.assertEqual(second["by_ticker"]["MISSING"], {
            "state": "missing",
            "reason": "no_assignment_effective_at_asof",
        })

    def test_repository_counts_review_assignments_separately_from_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            create_assignment_database(database)

            result = GroupAssignmentRepository(database).build(
                ["REVIEW"], asof="2026-07-01"
            )

        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["review_count"], 1)


if __name__ == "__main__":
    unittest.main()
