import unittest

from data.point_in_time_universe import (
    normalize_historical_components,
    normalize_symbol_changes,
)


class PointInTimeUniverseTest(unittest.TestCase):
    def test_normalizes_official_component_mapping_and_sorts_intervals(self):
        payload = {
            "1": {
                "Code": " twtr ",
                "Name": "Twitter Inc",
                "StartDate": "2018-06-07",
                "EndDate": "2022-10-28",
                "IsActiveNow": 0,
                "IsDelisted": 1,
            },
            "0": {
                "Code": "AAPL",
                "Name": "Apple Inc",
                "StartDate": "1982-11-30",
                "EndDate": None,
                "IsActiveNow": 1,
                "IsDelisted": 0,
            },
        }

        result = normalize_historical_components(payload)

        self.assertEqual([row.ticker for row in result], ["AAPL", "TWTR"])
        self.assertEqual(result[0].effective_from, "1982-11-30")
        self.assertIsNone(result[0].effective_to)
        self.assertTrue(result[0].is_active_now)
        self.assertFalse(result[0].is_delisted)
        self.assertEqual(result[1].effective_to, "2022-10-28")
        self.assertTrue(result[1].is_delisted)

    def test_accepts_nested_component_payload_and_identical_duplicate(self):
        row = {
            "Code": "AAL",
            "Name": "American Airlines Group",
            "StartDate": "2015-03-23",
            "EndDate": "2024-09-23",
            "IsActiveNow": 0,
            "IsDelisted": 0,
        }
        result = normalize_historical_components(
            {"HistoricalTickerComponents": {"0": row, "1": dict(row)}}
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ticker, "AAL")

    def test_rejects_empty_invalid_and_conflicting_component_payloads(self):
        invalid_cases = (
            {},
            [],
            {"0": {"Code": "", "StartDate": "2020-01-01"}},
            {
                "0": {
                    "Code": "AAA",
                    "StartDate": "2020-01-02",
                    "EndDate": "2020-01-02",
                }
            },
            {
                "0": {"Code": "AAA", "StartDate": "not-a-date"},
            },
            {
                "0": {"Code": "AAA", "StartDate": "2020-01-01"},
                "1": {
                    "Code": "AAA",
                    "StartDate": "2020-01-01",
                    "EndDate": "2021-01-01",
                },
            },
        )

        for payload in invalid_cases:
            with self.subTest(payload=payload):
                with self.assertRaises((TypeError, ValueError)):
                    normalize_historical_components(payload)

    def test_normalizes_symbol_changes_without_merging_identity(self):
        result = normalize_symbol_changes(
            [
                {
                    "exchange": "US",
                    "old_symbol": "FB",
                    "new_symbol": "META",
                    "company_name": "Meta Platforms Inc",
                    "effective": "2022-06-09",
                },
                {
                    "exchange": "US",
                    "old_symbol": " wyn ",
                    "new_symbol": "WH",
                    "company_name": "Wyndham Hotels",
                    "effective": "2018-06-01",
                },
            ]
        )

        self.assertEqual(
            [(row.old_symbol, row.new_symbol) for row in result],
            [("WYN", "WH"), ("FB", "META")],
        )
        self.assertEqual(result[1].effective_date, "2022-06-09")
        self.assertEqual(result[1].exchange, "US")

    def test_rejects_invalid_or_conflicting_symbol_changes(self):
        with self.assertRaises(ValueError):
            normalize_symbol_changes([])
        with self.assertRaises(ValueError):
            normalize_symbol_changes(
                [
                    {
                        "exchange": "US",
                        "old_symbol": "AAA",
                        "new_symbol": "BBB",
                        "effective": "2020-01-01",
                    },
                    {
                        "exchange": "US",
                        "old_symbol": "AAA",
                        "new_symbol": "CCC",
                        "effective": "2020-01-01",
                    },
                ]
            )
        with self.assertRaises(ValueError):
            normalize_symbol_changes(
                [
                    {
                        "exchange": "US",
                        "old_symbol": "AAA",
                        "new_symbol": "AAA",
                        "effective": "2020-01-01",
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
