import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from research.sec_identity_archive import (
    build_identity_index,
    find_sec_candidates,
    iter_submission_records,
)


class SecIdentityArchiveTests(unittest.TestCase):
    def test_archive_indexes_current_and_former_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submissions.zip"
            payload = {
                "cik": "123",
                "name": "Example Holdings, Inc.",
                "tickers": ["NEW"],
                "exchanges": ["Nasdaq"],
                "sic": "3674",
                "sicDescription": "Semiconductors",
                "formerNames": [
                    {
                        "name": "Example Devices Corp.",
                        "from": "2012-01-01",
                        "to": "2020-01-01",
                    }
                ],
                "filings": {
                    "recent": {"accessionNumber": []},
                    "files": [],
                },
            }
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "CIK0000000123.json",
                    json.dumps(payload),
                )

            index = build_identity_index(iter_submission_records(path))
            matches = find_sec_candidates(
                {"ticker": "OLD", "name": "Example Devices Corp."},
                index,
            )

        self.assertEqual(matches[0]["cik"], "0000000123")
        self.assertEqual(
            matches[0]["match_reasons"],
            ["exact_former_name"],
        )
        self.assertEqual(
            matches[0]["matched_former_name"]["from"],
            "2012-01-01",
        )

    def test_rejects_filename_payload_cik_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submissions.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "CIK0000000123.json",
                    json.dumps({"cik": "456"}),
                )

            with self.assertRaisesRegex(
                ValueError,
                r"CIK mismatch for CIK0000000123.json",
            ):
                tuple(iter_submission_records(path))

    def test_rejects_non_object_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submissions.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("CIK0000000123.json", "[]")

            with self.assertRaisesRegex(
                ValueError,
                r"SEC submission payload must be an object",
            ):
                tuple(iter_submission_records(path))

    def test_rejects_malformed_former_name_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submissions.zip"
            payload = {
                "cik": "123",
                "formerNames": [
                    {
                        "name": "Example",
                        "from": "not-a-date",
                        "to": "2020-01-01",
                    }
                ],
            }
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "CIK0000000123.json",
                    json.dumps(payload),
                )

            with self.assertRaises(ValueError):
                tuple(iter_submission_records(path))

    def test_shared_current_ticker_returns_all_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submissions.zip"
            with ZipFile(path, "w") as archive:
                for cik, name in ((123, "Alpha Inc"), (456, "Beta Inc")):
                    archive.writestr(
                        f"CIK{cik:010d}.json",
                        json.dumps(
                            {
                                "cik": str(cik),
                                "name": name,
                                "tickers": ["SAME"],
                            }
                        ),
                    )

            index = build_identity_index(iter_submission_records(path))
            matches = find_sec_candidates(
                {"ticker": "SAME", "name": "Unknown"},
                index,
            )

        self.assertEqual(
            [row["cik"] for row in matches],
            ["0000000123", "0000000456"],
        )
        self.assertEqual(
            [row["match_reasons"] for row in matches],
            [["current_ticker"], ["current_ticker"]],
        )

    def test_rejects_duplicate_cik_records(self):
        record = {
            "cik": "0000000123",
            "normalized_name": "EXAMPLE",
            "tickers": (),
            "former_names": (),
        }

        with self.assertRaisesRegex(
            ValueError,
            r"duplicate CIK record: 0000000123",
        ):
            build_identity_index([record, dict(record)])

    def test_normalizes_recent_filing_columns_for_header_sampling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submissions.zip"
            payload = {
                "cik": "123",
                "name": "Example",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000000123-20-000001"],
                        "filingDate": ["2020-03-01"],
                        "acceptanceDateTime": ["20200301210000"],
                        "form": ["10-K"],
                        "primaryDocument": ["example.htm"],
                    },
                    "files": [],
                },
            }
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "CIK0000000123.json",
                    json.dumps(payload),
                )

            record = tuple(iter_submission_records(path))[0]

        self.assertEqual(
            record["recent_filings"],
            (
                {
                    "accession_number": "0000000123-20-000001",
                    "filing_date": "2020-03-01",
                    "acceptance_datetime": "20200301210000",
                    "form": "10-K",
                    "primary_document": "example.htm",
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()
