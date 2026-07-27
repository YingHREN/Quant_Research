from pathlib import Path
import tempfile
import unittest

from research.delisted_reference_store import DelistedReferenceStore


def sample_row():
    return {
        "ticker": "OLD",
        "exchange": "NASDAQ",
        "name": "Example Inc",
        "provider_isin": "US123",
        "identity_panel": "strong_isin",
        "selection_hash": "b" * 64,
        "sample_version": "sample_v1",
        "valid_rows": 100,
        "last_date": "2020-01-01",
    }


class DelistedReferenceStoreTests(unittest.TestCase):
    def test_schema_contains_all_audit_and_quarantine_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.db"
            with DelistedReferenceStore(path) as store:
                names = {
                    row[0]
                    for row in store.connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }

        self.assertTrue(
            {
                "coverage_sample",
                "identity_evidence",
                "security_entity_links",
                "sec_industry_observations",
                "sec_industry_intervals",
                "provider_classification_snapshots",
                "market_behavior_classifications",
                "identity_conflicts",
                "rejected_industry_observations",
                "collection_runs",
                "source_artifacts",
            }.issubset(names)
        )

    def test_provider_snapshot_is_not_a_historical_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.db"
            with DelistedReferenceStore(path) as store:
                store.replace_sample(
                    [sample_row()],
                    "a" * 64,
                    "2026-07-27",
                )
                store.replace_provider_snapshots(
                    [
                        {
                            "ticker": "OLD",
                            "sector": "Technology",
                            "industry": "Software",
                            "snapshot_at": "2026-07-27T00:00:00Z",
                            "historical_eligibility": "snapshot_only",
                            "source": "eodhd",
                        }
                    ]
                )

                result = store.classification_asof(
                    "OLD",
                    "2019-01-01T00:00:00Z",
                )

        self.assertIsNone(result)

    def test_confirmed_link_reads_only_visible_sic_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.db"
            with DelistedReferenceStore(path) as store:
                store.replace_sample(
                    [sample_row()],
                    "a" * 64,
                    "2026-07-27",
                )
                store.replace_identity_results(
                    [
                        {
                            "ticker": "OLD",
                            "cik": "0000000123",
                            "link_status": "confirmed",
                            "decision_rule": "test_rule",
                            "rule_version": "test_v1",
                            "reason_codes": [],
                            "supporting_evidence": [],
                            "conflicting_evidence": [],
                        }
                    ],
                    [],
                )
                store.replace_sic_observations(
                    [
                        {
                            "cik": "0000000123",
                            "sic": "3674",
                            "industry_label": "Semiconductors",
                            "accession_number": "0000000123-18-000001",
                            "filing_date": "2018-03-01",
                            "accepted_at": "2018-03-01T21:00:00Z",
                            "available_at": "2018-03-01T21:00:00Z",
                            "source": "sec_edgar",
                            "parser_version": "parser_v1",
                        }
                    ],
                    [
                        {
                            "cik": "0000000123",
                            "sic": "3674",
                            "valid_from": "2018-03-01T21:00:00Z",
                            "valid_to": None,
                            "first_accession": "0000000123-18-000001",
                            "last_supporting_accession": "0000000123-18-000001",
                            "observation_count": 1,
                            "taxonomy_version": "sec_sic_v1",
                            "interval_rule_version": "interval_v1",
                        }
                    ],
                )

                before = store.classification_asof(
                    "OLD",
                    "2018-03-01T20:59:59Z",
                )
                after = store.classification_asof(
                    "OLD",
                    "2018-03-01T21:00:00Z",
                )

        self.assertIsNone(before)
        self.assertEqual(after["sic"], "3674")

    def test_failed_sample_replacement_rolls_back_and_store_is_healthy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.db"
            with DelistedReferenceStore(path) as store:
                store.replace_sample(
                    [sample_row()],
                    "a" * 64,
                    "2026-07-27",
                )
                duplicate = {
                    **sample_row(),
                    "ticker": "NEW",
                    "selection_hash": "c" * 64,
                }
                with self.assertRaises(Exception):
                    store.replace_sample(
                        [duplicate, dict(duplicate)],
                        "a" * 64,
                        "2026-07-27",
                    )
                tickers = [
                    row[0]
                    for row in store.connection.execute(
                        "SELECT ticker FROM coverage_sample"
                    )
                ]
                integrity = store.integrity_report()

        self.assertEqual(tickers, ["OLD"])
        self.assertEqual(
            integrity,
            {"integrity_check": "ok", "foreign_key_errors": 0},
        )

    def test_unknown_snapshot_ticker_is_rejected_by_foreign_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.db"
            with DelistedReferenceStore(path) as store:
                store.replace_sample(
                    [sample_row()],
                    "a" * 64,
                    "2026-07-27",
                )
                with self.assertRaises(Exception):
                    store.replace_provider_snapshots(
                        [
                            {
                                "ticker": "MISSING",
                                "sector": "Technology",
                                "industry": "Software",
                                "snapshot_at": "2026-07-27T00:00:00Z",
                                "historical_eligibility": "snapshot_only",
                                "source": "eodhd",
                            }
                        ]
                    )

    def test_replacing_same_sample_after_snapshot_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reference.db"
            with DelistedReferenceStore(path) as store:
                store.replace_sample(
                    [sample_row()],
                    "a" * 64,
                    "2026-07-27",
                )
                store.replace_provider_snapshots(
                    [
                        {
                            "ticker": "OLD",
                            "sector": "Technology",
                            "industry": "Software",
                            "snapshot_at": "2026-07-27T00:00:00Z",
                            "historical_eligibility": "snapshot_only",
                            "source": "eodhd",
                        }
                    ]
                )

                store.replace_sample(
                    [sample_row()],
                    "a" * 64,
                    "2026-07-27",
                )

                count = store.connection.execute(
                    "SELECT count(*) FROM coverage_sample"
                ).fetchone()[0]

        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
