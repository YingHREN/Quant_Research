import unittest

from research.delisted_history_pilot import (
    audit_history_rows,
    select_stratified_sample,
    summarize_pilot,
)


def catalog_row(code, exchange, *, currency="USD", type_="Common Stock"):
    return {
        "Code": code,
        "Name": f"{code} Company",
        "Country": "USA",
        "Exchange": exchange,
        "Currency": currency,
        "Type": type_,
        "Isin": None,
    }


class DelistedHistoryPilotTest(unittest.TestCase):
    def test_sample_filters_catalog_and_fulfils_each_exchange_quota(self):
        catalog = [
            catalog_row("N1", "NASDAQ"),
            catalog_row("N2", "NASDAQ"),
            catalog_row("Y1", "NYSE"),
            catalog_row("Y2", "NYSE"),
            catalog_row("A1", "NYSE MKT"),
            catalog_row("A2", "NYSE MKT"),
            catalog_row("BAD_OLD", "NASDAQ"),
            catalog_row("EUR1", "NYSE", currency="EUR"),
            catalog_row("ETF1", "NYSE", type_="ETF"),
            catalog_row("OTC1", "PINK"),
        ]

        sample = select_stratified_sample(
            catalog,
            {"NASDAQ": 1, "NYSE": 1, "NYSE MKT": 1},
        )

        self.assertEqual(len(sample), 3)
        self.assertEqual(
            {row["exchange"] for row in sample},
            {"NASDAQ", "NYSE", "NYSE MKT"},
        )
        self.assertTrue(
            all(
                row["ticker"]
                in {"N1", "N2", "Y1", "Y2", "A1", "A2"}
                for row in sample
            )
        )
        self.assertTrue(all(len(row["selection_hash"]) == 64 for row in sample))

    def test_sample_is_independent_of_catalog_order(self):
        catalog = [
            catalog_row(f"N{index}", "NASDAQ")
            for index in range(10)
        ] + [
            catalog_row(f"Y{index}", "NYSE")
            for index in range(10)
        ]

        forward = select_stratified_sample(
            catalog,
            {"NASDAQ": 3, "NYSE": 2},
        )
        backward = select_stratified_sample(
            list(reversed(catalog)),
            {"NASDAQ": 3, "NYSE": 2},
        )

        self.assertEqual(forward, backward)

    def test_sample_rejects_duplicate_codes_and_insufficient_stratum(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            select_stratified_sample(
                [
                    catalog_row("AAA", "NASDAQ"),
                    catalog_row("AAA", "NASDAQ"),
                ],
                {"NASDAQ": 1},
            )
        with self.assertRaisesRegex(ValueError, "NYSE"):
            select_stratified_sample(
                [catalog_row("AAA", "NASDAQ")],
                {"NASDAQ": 1, "NYSE": 1},
            )

    def test_history_audit_separates_duplicates_and_invalid_rows(self):
        sample = {
            "ticker": "AAA",
            "name": "AAA Company",
            "exchange": "NASDAQ",
        }
        payload = [
            {
                "date": "2017-12-29",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "adjusted_close": 10,
                "volume": 1000,
            },
            {
                "date": "2017-12-29",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "adjusted_close": 10,
                "volume": 1000,
            },
            {
                "date": "2018-01-02",
                "open": 11,
                "high": 12,
                "low": 10,
                "close": 11,
                "adjusted_close": 11,
                "volume": 1200,
            },
            {
                "date": "2018-01-03",
                "open": 11,
                "high": 10,
                "low": 12,
                "close": 11,
                "adjusted_close": 11,
                "volume": 1200,
            },
            {
                "date": "bad-date",
                "open": 11,
                "high": 12,
                "low": 10,
                "close": 11,
                "adjusted_close": 11,
                "volume": 1200,
            },
        ]

        result = audit_history_rows(sample, payload, raw_bytes=987)

        self.assertEqual(result["request_status"], "success")
        self.assertEqual(result["raw_rows"], 5)
        self.assertEqual(result["valid_rows"], 2)
        self.assertEqual(result["duplicate_dates"], 1)
        self.assertEqual(result["invalid_rows"], 2)
        self.assertEqual(result["first_date"], "2017-12-29")
        self.assertEqual(result["last_date"], "2018-01-02")
        self.assertEqual(result["post_2018_valid_rows"], 1)
        self.assertTrue(result["traded_since_2018"])
        self.assertEqual(result["raw_bytes"], 987)

    def test_history_audit_reports_empty_and_suspicious_security(self):
        result = audit_history_rows(
            {
                "ticker": "ABC-WT",
                "name": "ABC Warrants",
                "exchange": "NYSE",
            },
            [],
            raw_bytes=3,
        )

        self.assertEqual(result["request_status"], "empty")
        self.assertEqual(result["valid_rows"], 0)
        self.assertTrue(result["suspicious_security_label"])
        self.assertIsNone(result["first_date"])

    def test_summary_extrapolates_each_exchange_from_fixed_sample(self):
        catalog = [
            catalog_row(f"N{index}", "NASDAQ") for index in range(4)
        ] + [catalog_row(f"Y{index}", "NYSE") for index in range(2)]
        sample = (
            {
                "ticker": "N0",
                "exchange": "NASDAQ",
            },
            {
                "ticker": "N1",
                "exchange": "NASDAQ",
            },
            {
                "ticker": "Y0",
                "exchange": "NYSE",
            },
        )
        audits = (
            {
                "ticker": "N0",
                "exchange": "NASDAQ",
                "request_status": "success",
                "valid_rows": 10,
                "raw_bytes": 100,
                "traded_since_2018": True,
            },
            {
                "ticker": "N1",
                "exchange": "NASDAQ",
                "request_status": "empty",
                "valid_rows": 0,
                "raw_bytes": 2,
                "traded_since_2018": False,
            },
            {
                "ticker": "Y0",
                "exchange": "NYSE",
                "request_status": "success",
                "valid_rows": 20,
                "raw_bytes": 200,
                "traded_since_2018": False,
            },
        )

        result = summarize_pilot(sample, audits, catalog)
        by_exchange = {
            row["exchange"]: row for row in result["by_exchange"]
        }

        self.assertEqual(by_exchange["NASDAQ"]["eligible_catalog"], 4)
        self.assertEqual(by_exchange["NASDAQ"]["sample_count"], 2)
        self.assertEqual(by_exchange["NASDAQ"]["success_rate"], 0.5)
        self.assertEqual(
            by_exchange["NASDAQ"]["estimated_successful_tickers"],
            2,
        )
        self.assertEqual(
            by_exchange["NASDAQ"]["estimated_raw_bytes_mean"],
            200,
        )
        self.assertEqual(by_exchange["NYSE"]["eligible_catalog"], 2)
        self.assertEqual(
            by_exchange["NYSE"]["estimated_raw_bytes_mean"],
            400,
        )
        self.assertEqual(result["estimated_successful_tickers"], 4)
        self.assertEqual(result["estimated_raw_bytes_mean"], 600)


if __name__ == "__main__":
    unittest.main()
