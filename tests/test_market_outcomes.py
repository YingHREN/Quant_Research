import unittest

import numpy as np
import pandas as pd

from research.market_outcomes import (
    attach_market_outcomes,
    calibrate_score_probability,
    eligible_outcome_rows,
)


def score_frame(index, score=70.0, atr20_pct=2.0):
    multi = pd.MultiIndex.from_product(
        (("AMD",), index),
        names=("ticker", "observation_date"),
    )
    return pd.DataFrame(
        {
            "reversal_opportunity_score": score,
            "reversal_opportunity_coverage": 1.0,
            "downside_risk_score": score,
            "downside_risk_coverage": 1.0,
            "atr20_pct": atr20_pct,
        },
        index=multi,
    )


def calibration_frame(size, classes):
    dates = pd.bdate_range("2025-01-02", periods=size)
    frame = score_frame(dates, score=50.0)
    frame["reversal_opportunity_score"] = np.linspace(0.0, 100.0, size)
    frame["opportunity_outcome_5"] = (
        np.arange(size) % 2 if classes else np.zeros(size)
    )
    frame["opportunity_label_end_date_5"] = dates
    return frame


class MarketOutcomeTest(unittest.TestCase):
    def test_empty_score_frame_returns_typed_empty_outcome_columns(self):
        index = pd.MultiIndex.from_arrays(
            ([], []),
            names=("ticker", "observation_date"),
        )
        scores = pd.DataFrame(
            columns=(
                "reversal_opportunity_score",
                "downside_risk_score",
                "atr20_pct",
            ),
            index=index,
        )

        result = attach_market_outcomes(scores, {}, horizons=(5,))

        self.assertTrue(result.empty)
        for column in (
            "opportunity_outcome_5",
            "downside_risk_outcome_5",
            "opportunity_label_end_date_5",
            "downside_risk_label_end_date_5",
        ):
            self.assertIn(column, result)
        self.assertEqual(
            result.index.names,
            ["ticker", "observation_date"],
        )

    def test_opportunity_and_risk_labels_can_both_be_true(self):
        index = pd.bdate_range("2025-01-02", periods=70)
        close = pd.Series(100.0, index=index)
        close.iloc[51] = 95.0
        close.iloc[55] = 103.0
        frame = score_frame(index, score=70.0, atr20_pct=2.0)

        result = attach_market_outcomes(
            frame,
            {"AMD": pd.DataFrame({"Close": close})},
            horizons=(5,),
        )
        row = result.loc[("AMD", index[50])]

        self.assertEqual(row["opportunity_outcome_5"], 1.0)
        self.assertEqual(row["downside_risk_outcome_5"], 1.0)
        self.assertEqual(row["opportunity_label_end_date_5"], index[55])
        self.assertEqual(row["downside_risk_label_end_date_5"], index[55])
        self.assertTrue(result["opportunity_outcome_5"].iloc[-5:].isna().all())
        self.assertTrue(
            result["downside_risk_outcome_5"].iloc[-5:].isna().all()
        )

    def test_eligibility_requires_label_end_strictly_before_asof(self):
        frame = calibration_frame(120, classes=True)
        cutoff = frame["opportunity_label_end_date_5"].iloc[100]

        eligible = eligible_outcome_rows(
            frame,
            cutoff,
            5,
            "opportunity",
        )

        self.assertTrue(
            (eligible["opportunity_label_end_date_5"] < cutoff).all()
        )
        self.assertEqual(len(eligible), 100)

    def test_calibration_requires_one_hundred_samples_and_both_classes(self):
        too_small = calibration_frame(99, classes=True)
        one_class = calibration_frame(120, classes=False)

        self.assertEqual(
            calibrate_score_probability(
                too_small,
                70.0,
                "2026-07-23",
                5,
                "opportunity",
            ).reason,
            "insufficient_calibration_samples",
        )
        self.assertEqual(
            calibrate_score_probability(
                one_class,
                70.0,
                "2026-07-23",
                5,
                "opportunity",
            ).reason,
            "calibration_requires_both_classes",
        )

    def test_monotonic_calibration_uses_only_matured_rows(self):
        frame = calibration_frame(120, classes=True)
        frame["opportunity_outcome_5"] = (
            frame["reversal_opportunity_score"] >= 50.0
        ).astype(float)

        low = calibrate_score_probability(
            frame,
            10.0,
            "2026-07-23",
            5,
            "opportunity",
        )
        high = calibrate_score_probability(
            frame,
            90.0,
            "2026-07-23",
            5,
            "opportunity",
        )

        self.assertIsNone(low.reason)
        self.assertIsNone(high.reason)
        self.assertLess(low.probability, high.probability)
        self.assertEqual(high.sample_count, 120)


if __name__ == "__main__":
    unittest.main()
