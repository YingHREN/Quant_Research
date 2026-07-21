import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.build_events import build_event_table
from research.events import EventTransition, VCPEvent
from research.vcp import ContractionLeg, VCPPattern
from tests.helpers import make_ohlcv


def _fixture_event(history):
    observation_date = history.index[260]
    first_seen = history.index[255]
    leg = ContractionLeg(
        peak_date=history.index[230],
        trough_date=history.index[240],
        peak=125.0,
        trough=112.0,
        depth_pct=10.4,
        mean_volume=900_000,
    )
    pattern = VCPPattern(
        asof_date=first_seen,
        accepted=True,
        stage="forming",
        base_start=history.index[220],
        base_end=first_seen,
        legs=(leg,),
        pending_leg=None,
        pivot=126.0,
        pivot_date=history.index[250],
        distance_to_pivot_pct=-4.0,
        reject_reason=None,
        metrics={
            "base_depth_pct": 15.0,
            "last_first_ratio": 0.6,
            "contraction_slope": -3.0,
            "terminal_range_pct": 4.0,
            "volume_dryup_ratio": 0.7,
        },
    )
    return VCPEvent(
        event_id="event-1",
        ticker="TEST",
        base_start=history.index[220],
        first_seen=first_seen,
        initial_pattern=pattern,
        near_pivot_date=observation_date,
        breakout_pivot=126.0,
        transitions=[
            EventTransition(first_seen, "forming", 126.0, 120.0),
            EventTransition(observation_date, "near_pivot", 126.0, 123.0),
        ],
    )


class EventTableTest(unittest.TestCase):
    def test_schema_time_order_and_unique_primary_rows(self):
        history = make_ohlcv(np.linspace(80, 150, 340))
        benchmark = make_ohlcv(np.linspace(200, 260, 340))
        event = _fixture_event(history)

        with patch("research.build_events.scan_ticker_events", return_value=[event]):
            table = build_event_table(
                {"TEST": history}, benchmark, ["TEST"], stages=("near_pivot",)
            )

        required = {
            "event_id", "ticker", "observation_stage", "observation_date", "entry_date",
            "base_start", "pivot", "mom_3_1", "mom_6_1", "mom_12_1",
            "rel_ret_20", "rel_ret_40", "rel_ret_60", "barrier_label",
            "detector_version", "feature_spec_version",
        }
        self.assertTrue(required.issubset(table.columns))
        self.assertTrue((table.observation_date < table.entry_date).all())
        self.assertFalse(table.event_id.duplicated().any())
        self.assertEqual(table.iloc[0].observation_stage, "near_pivot")


if __name__ == "__main__":
    unittest.main()
