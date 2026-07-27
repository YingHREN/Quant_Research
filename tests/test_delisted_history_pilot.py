import unittest

from research.delisted_history_pilot import select_stratified_sample


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


if __name__ == "__main__":
    unittest.main()
