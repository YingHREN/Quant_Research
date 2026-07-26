import unittest

import numpy as np
import pandas as pd

from research.relative_strength import (
    MODEL_VERSION,
    build_relative_strength_snapshot,
)


def _prices(ticker, final_multiple, periods=253):
    dates = pd.bdate_range(end="2026-07-24", periods=periods)
    values = np.geomspace(100.0, 100.0 * final_multiple, periods)
    return pd.DataFrame(
        {
            "ticker": ticker,
            "date": dates,
            "adjusted_close": values,
        }
    )


class RelativeStrengthTest(unittest.TestCase):
    def test_weighted_returns_percentile_and_short_history_exclusion(self):
        prices = pd.concat(
            [
                _prices("FAST", 2.0),
                _prices("SLOW", 1.1),
                _prices("SHORT", 3.0, periods=252),
            ],
            ignore_index=True,
        )

        result = build_relative_strength_snapshot(prices, "2026-07-24")

        self.assertEqual(list(result["ticker"]), ["FAST", "SLOW"])
        self.assertEqual(set(result["model_version"]), {MODEL_VERSION})
        self.assertEqual(set(result["sample_count"]), {2})
        by_ticker = result.set_index("ticker")
        self.assertEqual(by_ticker.loc["FAST", "rs_rating"], 99)
        self.assertEqual(by_ticker.loc["SLOW", "rs_rating"], 50)
        for ticker in ("FAST", "SLOW"):
            row = by_ticker.loc[ticker]
            expected = (
                0.4 * row["return_63"]
                + 0.2 * row["return_126"]
                + 0.2 * row["return_189"]
                + 0.2 * row["return_252"]
            )
            self.assertAlmostEqual(row["composite"], expected)

    def test_rejects_duplicate_keys_and_does_not_fill_missing_prices(self):
        prices = _prices("AAA", 2.0)
        duplicate = pd.concat([prices, prices.iloc[-1:]], ignore_index=True)
        with self.assertRaises(ValueError):
            build_relative_strength_snapshot(duplicate, "2026-07-24")

        missing = prices.copy()
        missing.loc[0, "adjusted_close"] = np.nan
        result = build_relative_strength_snapshot(missing, "2026-07-24")
        self.assertTrue(result.empty)


if __name__ == "__main__":
    unittest.main()
