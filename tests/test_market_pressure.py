import unittest

import numpy as np
import pandas as pd

from research.market_pressure import Evidence, build_pressure_rows


def history(close, high=None, low=None, volume=None):
    close = np.asarray(close, dtype=float)
    index = pd.bdate_range("2025-01-02", periods=len(close))
    high = close + 1.0 if high is None else np.asarray(high, dtype=float)
    low = close - 1.0 if low is None else np.asarray(low, dtype=float)
    volume = (
        np.full(len(close), 100.0)
        if volume is None
        else np.asarray(volume, dtype=float)
    )
    return pd.DataFrame(
        {
            "Open": close,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


class MarketPressureTest(unittest.TestCase):
    def test_close_location_and_signed_volume_have_expected_direction(self):
        frame = history(
            [10.0] * 20 + [11.8],
            high=[11.0] * 20 + [12.0],
            low=[9.0] * 20 + [10.0],
            volume=[100.0] * 20 + [200.0],
        )

        row = build_pressure_rows(frame).iloc[-1]

        self.assertAlmostEqual(row["close_location"], 0.8)
        self.assertAlmostEqual(row["volume_ratio"], 2.0)
        self.assertAlmostEqual(row["signed_volume_proxy"], 1.6)

    def test_high_volume_non_progress_and_failed_breakout_are_separate(self):
        close = list(np.linspace(90.0, 100.0, 21)) + [99.7]
        frame = history(
            close,
            high=list(np.linspace(91.0, 101.0, 21)) + [103.0],
            low=list(np.linspace(89.0, 99.0, 21)) + [98.0],
            volume=[100.0] * 21 + [220.0],
        )

        row = build_pressure_rows(frame).iloc[-1]

        self.assertTrue(row["failed_breakout"])
        self.assertTrue(row["high_volume_non_progress"])

    def test_appending_future_rows_cannot_change_prior_pressure_rows(self):
        base = history(np.linspace(80.0, 100.0, 60))
        future = history([300.0, 20.0]).set_axis(
            pd.bdate_range(base.index[-1] + pd.Timedelta(days=1), periods=2)
        )
        extended = pd.concat([base, future])

        expected = build_pressure_rows(base)
        actual = build_pressure_rows(extended).loc[base.index]

        pd.testing.assert_frame_equal(actual, expected)

    def test_zero_range_row_is_unavailable_not_infinite(self):
        frame = history(
            [10.0] * 21,
            high=[10.0] * 21,
            low=[10.0] * 21,
        )

        row = build_pressure_rows(frame).iloc[-1]

        self.assertTrue(np.isnan(row["close_location"]))
        self.assertTrue(np.isnan(row["upper_wick_ratio"]))

    def test_unavailable_evidence_requires_reason_and_freezes_metadata(self):
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            Evidence(
                key="volume_ratio",
                value=None,
                threshold=1.2,
                state="unavailable",
                points=0.0,
                max_points=5.0,
                window="20 sessions",
            )

        evidence = Evidence(
            key="volume_ratio",
            value=1.5,
            threshold=1.2,
            state="met",
            points=5.0,
            max_points=5.0,
            window="20 sessions",
            metadata={"source": "daily_proxy"},
        )
        with self.assertRaises(TypeError):
            evidence.metadata["source"] = "changed"


if __name__ == "__main__":
    unittest.main()
