import unittest

from research.sec_industry_history import (
    build_sic_intervals,
    classification_asof,
    parse_sec_submission_header,
)


class SecIndustryIntervalTests(unittest.TestCase):
    def test_interval_starts_only_when_filing_is_available(self):
        observations = [
            {
                "cik": "0000000123",
                "sic": "3674",
                "available_at": "2018-03-01T21:00:00Z",
                "accession_number": "0000000123-18-000001",
            },
            {
                "cik": "0000000123",
                "sic": "7372",
                "available_at": "2020-04-01T20:00:00Z",
                "accession_number": "0000000123-20-000002",
            },
        ]

        intervals = build_sic_intervals(observations)

        self.assertIsNone(
            classification_asof(
                intervals,
                "2018-02-28T23:59:59Z",
            )
        )
        self.assertEqual(
            classification_asof(
                intervals,
                "2019-01-01T00:00:00Z",
            )["sic"],
            "3674",
        )
        self.assertEqual(
            classification_asof(
                intervals,
                "2021-01-01T00:00:00Z",
            )["sic"],
            "7372",
        )

    def test_rejects_conflicting_sic_at_same_available_time(self):
        observations = [
            {
                "cik": "0000000123",
                "sic": "3674",
                "available_at": "2018-03-01T21:00:00Z",
                "accession_number": "0000000123-18-000001",
            },
            {
                "cik": "0000000123",
                "sic": "7372",
                "available_at": "2018-03-01T21:00:00Z",
                "accession_number": "0000000123-18-000002",
            },
        ]

        with self.assertRaisesRegex(
            ValueError,
            r"conflicting SIC observations at the same available time",
        ):
            build_sic_intervals(observations)

    def test_rejects_duplicate_accession_with_conflicting_sic(self):
        observations = [
            {
                "cik": "0000000123",
                "sic": "3674",
                "available_at": "2018-03-01T21:00:00Z",
                "accession_number": "0000000123-18-000001",
            },
            {
                "cik": "0000000123",
                "sic": "7372",
                "available_at": "2018-03-02T21:00:00Z",
                "accession_number": "0000000123-18-000001",
            },
        ]

        with self.assertRaisesRegex(
            ValueError,
            r"duplicate accession has conflicting SIC",
        ):
            build_sic_intervals(observations)

    def test_same_sic_observations_reinforce_one_open_interval(self):
        intervals = build_sic_intervals(
            [
                {
                    "cik": "0000000123",
                    "sic": "3674",
                    "available_at": "2018-03-01T21:00:00Z",
                    "accession_number": "0000000123-18-000001",
                },
                {
                    "cik": "0000000123",
                    "sic": "3674",
                    "available_at": "2019-03-01T21:00:00Z",
                    "accession_number": "0000000123-19-000001",
                },
            ]
        )

        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["observation_count"], 2)
        self.assertEqual(
            intervals[0]["last_supporting_accession"],
            "0000000123-19-000001",
        )
        self.assertIsNone(intervals[0]["valid_to"])

    def test_rejects_timezone_naive_asof(self):
        with self.assertRaisesRegex(
            ValueError,
            r"timestamp must include timezone",
        ):
            classification_asof((), "2020-01-01T00:00:00")


class SecIndustryHeaderTests(unittest.TestCase):
    def test_parses_explicit_sic_from_submission_header(self):
        result = parse_sec_submission_header(
            """
            COMPANY CONFORMED NAME: EXAMPLE DEVICES INC
            STANDARD INDUSTRIAL CLASSIFICATION:
                SEMICONDUCTORS & RELATED DEVICE MFG [3674]
            """,
            "0000000123-18-000001",
            "2018-03-01",
            "2018-03-01T21:00:00Z",
        )

        self.assertEqual(result["cik"], "0000000123")
        self.assertEqual(result["sic"], "3674")
        self.assertEqual(
            result["industry_label"],
            "SEMICONDUCTORS & RELATED DEVICE MFG",
        )
        self.assertEqual(
            result["available_at"],
            "2018-03-01T21:00:00Z",
        )

    def test_missing_sic_header_returns_none(self):
        result = parse_sec_submission_header(
            "COMPANY CONFORMED NAME: EXAMPLE",
            "0000000123-18-000001",
            "2018-03-01",
            "2018-03-01T21:00:00Z",
        )
        self.assertIsNone(result)

    def test_conflicting_sic_header_lines_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            r"conflicting SIC lines in submission header",
        ):
            parse_sec_submission_header(
                """
                STANDARD INDUSTRIAL CLASSIFICATION: DEVICES [3674]
                STANDARD INDUSTRIAL CLASSIFICATION: SOFTWARE [7372]
                """,
                "0000000123-18-000001",
                "2018-03-01",
                "2018-03-01T21:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
