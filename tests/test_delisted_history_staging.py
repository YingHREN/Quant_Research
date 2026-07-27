import math
import unittest

from research.delisted_history_staging import (
    partition_history_rows,
    summarize_partitions,
)


def daily(date, *, open_=10, high=12, low=9, close=11, adjusted=11, volume=100):
    return {
        "date": date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "adjusted_close": adjusted,
        "volume": volume,
    }


class DelistedHistoryStagingTest(unittest.TestCase):
    def test_partition_preserves_valid_rows_and_classifies_rejections(self):
        payload = [
            daily("2020-01-02"),
            daily("bad-date"),
            daily("2020-01-02"),
            daily("2020-01-03", open_=math.nan),
            daily("2020-01-04", close=0, adjusted=0),
            daily("2020-01-05", high=8),
            daily("2020-01-06", volume=-1),
        ]

        valid, rejected = partition_history_rows(payload)

        self.assertEqual(valid, [payload[0]])
        self.assertEqual(
            [row.reason for row in rejected],
            [
                "invalid_date",
                "duplicate_date",
                "invalid_numeric",
                "non_positive_price",
                "invalid_ohlc",
                "negative_volume",
            ],
        )
        self.assertEqual([row.source_index for row in rejected], list(range(1, 7)))
        self.assertIn('"date":"bad-date"', rejected[0].raw_json)

    def test_first_invalid_numeric_date_still_reserves_duplicate_date(self):
        payload = [
            daily("2020-01-02", open_=math.nan),
            daily("2020-01-02"),
        ]

        valid, rejected = partition_history_rows(payload)

        self.assertEqual(valid, [])
        self.assertEqual(
            [row.reason for row in rejected],
            ["invalid_numeric", "duplicate_date"],
        )

    def test_non_mapping_row_is_rejected_with_stable_json(self):
        valid, rejected = partition_history_rows(["bad"])

        self.assertEqual(valid, [])
        self.assertEqual(rejected[0].reason, "invalid_row")
        self.assertEqual(rejected[0].raw_json, '"bad"')

    def test_summary_counts_rows_and_reasons(self):
        result = summarize_partitions(
            [
                {
                    "exchange": "NASDAQ",
                    "ticker": "AAA",
                    "raw_rows": 3,
                    "valid_rows": 2,
                    "rejected_rows": 1,
                    "reason_counts": {"invalid_date": 1},
                },
                {
                    "exchange": "NYSE",
                    "ticker": "BBB",
                    "raw_rows": 2,
                    "valid_rows": 0,
                    "rejected_rows": 2,
                    "reason_counts": {"invalid_ohlc": 2},
                },
            ]
        )

        self.assertEqual(result["security_count"], 2)
        self.assertEqual(result["raw_rows"], 5)
        self.assertEqual(result["valid_rows"], 2)
        self.assertEqual(result["rejected_rows"], 3)
        self.assertEqual(
            result["reason_counts"],
            {"invalid_date": 1, "invalid_ohlc": 2},
        )
        self.assertEqual(result["by_exchange"]["NASDAQ"]["valid_rows"], 2)

    def test_summary_rejects_non_conserving_partition(self):
        with self.assertRaisesRegex(ValueError, "conserve"):
            summarize_partitions(
                [
                    {
                        "exchange": "NASDAQ",
                        "ticker": "AAA",
                        "raw_rows": 2,
                        "valid_rows": 2,
                        "rejected_rows": 1,
                        "reason_counts": {"invalid_date": 1},
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
