from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from zipfile import ZipFile

from run_delisted_identity_coverage import (
    collect_artifact,
    collect_provider_sample,
    collect_sec_header_sample,
    main,
    run_coverage_pilot,
)


def sec_zip_bytes():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "CIK0000000123.json",
            '{"cik":"123","name":"Example Inc"}',
        )
    return buffer.getvalue()


class CollectArtifactTests(unittest.TestCase):
    def test_second_run_reuses_verified_cache(self):
        calls = []
        payload = sec_zip_bytes()

        def fetch(url, headers):
            calls.append((url, headers))
            return payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = collect_artifact(
                "sec_submissions",
                root,
                fetcher=fetch,
            )
            second = collect_artifact(
                "sec_submissions",
                root,
                fetcher=fetch,
            )

        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["byte_count"], len(payload))
        self.assertEqual(len(calls), 1)
        self.assertTrue(second["reused"])

    def test_corrupt_cached_artifact_is_rejected_without_refresh(self):
        calls = []
        payload = sec_zip_bytes()

        def fetch(url, headers):
            calls.append(url)
            return payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            collect_artifact("sec_submissions", root, fetcher=fetch)
            (root / "submissions.zip").write_bytes(b"tampered")

            with self.assertRaisesRegex(
                ValueError,
                r"cached artifact failed content verification",
            ):
                collect_artifact("sec_submissions", root, fetcher=fetch)

        self.assertEqual(len(calls), 1)

    def test_real_sec_collection_requires_descriptive_user_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(
                    ValueError,
                    r"SEC_USER_AGENT is required",
                ):
                    collect_artifact(
                        "sec_submissions",
                        Path(temp_dir),
                        fetcher=None,
                    )

    def test_non_zip_sec_response_is_rejected_before_cache_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(
                ValueError,
                r"SEC submissions artifact must be a valid ZIP",
            ):
                collect_artifact(
                    "sec_submissions",
                    root,
                    fetcher=lambda url, headers: b"<html>blocked</html>",
                )

            self.assertFalse((root / "submissions.zip").exists())


class CollectProviderSampleTests(unittest.TestCase):
    def test_quota_error_is_resumable_and_success_cache_is_reused(self):
        rows = [
            {"ticker": "AAA", "link_status": "unresolved"},
            {"ticker": "BBB", "link_status": "unresolved"},
            {"ticker": "CCC", "link_status": "confirmed"},
        ]
        calls = []

        def first_fetch(ticker, token):
            calls.append(ticker)
            if ticker == "AAA":
                raise HTTPError(
                    "https://provider.invalid",
                    429,
                    "quota",
                    {},
                    None,
                )
            return {
                "General": {
                    "Code": ticker,
                    "ISIN": "US123",
                    "CIK": "123",
                    "Sector": "Technology",
                    "Industry": "Software",
                }
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = collect_provider_sample(
                rows,
                root,
                token="fake-secret-token",
                fetcher=first_fetch,
            )
            second_calls = []

            def second_fetch(ticker, token):
                second_calls.append(ticker)
                return {
                    "General": {
                        "Code": ticker,
                        "ISIN": "US456",
                        "CIK": "456",
                    }
                }

            second = collect_provider_sample(
                rows,
                root,
                token="fake-secret-token",
                fetcher=second_fetch,
            )

        self.assertEqual(calls, ["AAA", "BBB"])
        self.assertEqual(first["status_counts"], {"quota_exhausted": 1, "success": 1})
        self.assertEqual(second_calls, ["AAA"])
        self.assertEqual(second["status_counts"], {"success": 2})
        self.assertNotIn("fake-secret-token", repr(first))
        self.assertNotIn("fake-secret-token", repr(second))


class CollectSecHeaderSampleTests(unittest.TestCase):
    def test_confirmed_cik_filing_header_is_cached_and_parsed(self):
        index = {
            "by_cik": {
                "0000000123": {
                    "cik": "0000000123",
                    "recent_filings": (
                        {
                            "accession_number": "0000000123-20-000001",
                            "filing_date": "2020-03-01",
                            "acceptance_datetime": "2020-03-01T21:00:00Z",
                            "form": "10-K",
                            "primary_document": "example.htm",
                        },
                    ),
                }
            }
        }
        decisions = [
            {
                "ticker": "OLD",
                "cik": "0000000123",
                "link_status": "confirmed",
            }
        ]
        calls = []

        def fetch(cik, accession, headers):
            calls.append((cik, accession))
            return (
                "STANDARD INDUSTRIAL CLASSIFICATION: "
                "SEMICONDUCTORS [3674]"
            ).encode()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = collect_sec_header_sample(
                decisions,
                index,
                root,
                fetcher=fetch,
            )
            second = collect_sec_header_sample(
                decisions,
                index,
                root,
                fetcher=fetch,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(first["observations"][0]["sic"], "3674")
        self.assertEqual(second["status_counts"], {"success": 1})


class RunCoveragePilotTests(unittest.TestCase):
    def test_offline_pilot_with_missing_sec_cache_makes_no_network_call(self):
        calls = []

        def fetch(url, headers):
            calls.append(url)
            return sec_zip_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "securities": [
                            {
                                "ticker": "OLD",
                                "exchange": "NASDAQ",
                                "name": "Example",
                                "identity_status": "ticker_only",
                                "provider_isin": None,
                                "backfill_eligible": True,
                            }
                        ]
                    }
                )
            )
            staging_path = root / "delisted.db"
            connection = sqlite3.connect(staging_path)
            connection.execute(
                """
                CREATE TABLE history_segments (
                    ticker TEXT, first_date TEXT, last_date TEXT,
                    row_count INTEGER
                )
                """
            )
            connection.commit()
            connection.execute(
                "INSERT INTO history_segments VALUES (?, ?, ?, ?)",
                ("OLD", "2018-01-01", "2020-01-01", 500),
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(
                ValueError,
                r"offline SEC submissions cache is missing",
            ):
                run_coverage_pilot(
                    catalog_path,
                    staging_path,
                    root / "reference.db",
                    root / "raw",
                    quotas={"ticker_only": 1},
                    snapshot_date="2026-07-27",
                    sec_fetcher=fetch,
                    offline=True,
                )

        self.assertEqual(calls, [])

    def test_offline_pilot_freezes_sample_and_writes_reference_db(self):
        catalog = {
            "securities": [
                {
                    "ticker": "OLD",
                    "exchange": "NASDAQ",
                    "name": "Example Inc",
                    "identity_status": "ticker_only",
                    "provider_isin": None,
                    "backfill_eligible": True,
                }
            ]
        }
        sec_payload = {
            "cik": "123",
            "name": "Example Holdings Inc",
            "tickers": ["OLD"],
            "exchanges": ["Nasdaq"],
            "formerNames": [],
            "filings": {"recent": {}, "files": []},
        }
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr(
                "CIK0000000123.json",
                json.dumps(sec_payload),
            )
        calls = []

        def sec_fetcher(url, headers):
            calls.append(url)
            return buffer.getvalue()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(catalog))
            staging_path = root / "delisted.db"
            connection = sqlite3.connect(staging_path)
            connection.execute(
                """
                CREATE TABLE history_segments (
                    ticker TEXT, first_date TEXT, last_date TEXT,
                    row_count INTEGER
                )
                """
            )
            connection.execute(
                "INSERT INTO history_segments VALUES (?, ?, ?, ?)",
                ("OLD", "2018-01-01", "2020-01-01", 500),
            )
            connection.commit()
            connection.close()
            reference_path = root / "reference.db"
            raw_root = root / "raw"
            collect_artifact(
                "sec_submissions",
                raw_root / "sec",
                fetcher=sec_fetcher,
            )

            first = run_coverage_pilot(
                catalog_path,
                staging_path,
                reference_path,
                raw_root,
                quotas={"ticker_only": 1},
                snapshot_date="2026-07-27",
                sec_fetcher=lambda url, headers: self.fail(
                    "offline run attempted SEC network access"
                ),
                offline=True,
            )
            second = run_coverage_pilot(
                catalog_path,
                staging_path,
                reference_path,
                raw_root,
                quotas={"ticker_only": 1},
                snapshot_date="2026-07-27",
                sec_fetcher=lambda url, headers: self.fail(
                    "offline rerun attempted SEC network access"
                ),
                offline=True,
            )

            stored_status = sqlite3.connect(reference_path).execute(
                "SELECT link_status FROM security_entity_links"
            ).fetchone()[0]

        self.assertEqual(first["sample_count"], 1)
        self.assertEqual(first["decision_counts"], {"review_required": 1})
        self.assertEqual(second["sample_sha256"], first["sample_sha256"])
        self.assertEqual(stored_status, "review_required")
        self.assertEqual(len(calls), 1)


class CommandLineTests(unittest.TestCase):
    def test_main_prints_a_secret_free_machine_readable_summary(self):
        expected = {
            "sample_count": 1,
            "decision_counts": {"review_required": 1},
            "decisions": [{"ticker": "OLD"}],
        }
        with patch(
            "run_delisted_identity_coverage.run_coverage_pilot",
            return_value=expected,
        ) as run:
            with patch.dict(
                os.environ,
                {"EODHD_API_TOKEN": "do-not-print"},
                clear=False,
            ):
                with patch("builtins.print") as emit:
                    status = main(
                        [
                            "--catalog",
                            "catalog.json",
                            "--delisted-db",
                            "delisted.db",
                            "--reference-db",
                            "reference.db",
                            "--raw-root",
                            "raw",
                            "--offline",
                        ]
                    )

        self.assertEqual(status, 0)
        self.assertTrue(run.call_args.kwargs["offline"])
        output = emit.call_args.args[0]
        self.assertEqual(
            json.loads(output),
            {
                "decision_counts": {"review_required": 1},
                "sample_count": 1,
            },
        )
        self.assertNotIn("do-not-print", output)


if __name__ == "__main__":
    unittest.main()
