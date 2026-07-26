from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.entry_signals import build_entry_signal_rows
from research.vcp import VCPPattern
from tests.helpers import make_ohlcv
from tests.test_vcp import textbook_vcp_fixture


def _pattern(frame, *, accepted=True, pivot=10.0, reason=None):
    date = pd.Timestamp(frame.index[-1])
    return VCPPattern(
        asof_date=date,
        accepted=accepted,
        stage="forming" if accepted else "none",
        base_start=pd.Timestamp(frame.index[0]) if accepted else None,
        base_end=date,
        legs=(),
        pending_leg=None,
        pivot=pivot if accepted else None,
        pivot_date=pd.Timestamp(frame.index[-2]) if accepted else None,
        distance_to_pivot_pct=(
            (float(frame["Close"].iloc[-1]) / pivot - 1) * 100
            if accepted
            else None
        ),
        reject_reason=reason,
        metrics={},
    )


def _accepted_after_sixty(frame):
    if len(frame) < 60:
        return _pattern(
            frame,
            accepted=False,
            reason="insufficient_history",
        )
    return _pattern(frame)


class EntrySignalHistoryTest(unittest.TestCase):
    def test_returns_one_ordered_row_per_input_date(self):
        history = textbook_vcp_fixture()

        rows = build_entry_signal_rows(history)

        self.assertEqual(
            [row["time"] for row in rows],
            [date.date().isoformat() for date in history.index],
        )
        self.assertTrue(
            {
                "strict_vcp_active",
                "strict_vcp_start",
                "strict_vcp_evidence",
                "tight_platform_active",
                "tight_platform_start",
                "tight_platform_evidence",
                "vcp_breakout_confirmed",
                "vcp_breakout_price_confirmed",
                "vcp_breakout_volume_confirmed",
                "vcp_breakout_buy_zone_confirmed",
                "pocket_pivot",
                "pocket_pivot_evidence",
            }.issubset(rows[-1])
        )

    def test_first_seen_is_once_and_later_crossing_confirms_breakout(self):
        history = make_ohlcv(
            [9.0] * 60 + [9.5, 10.1, 10.2],
            volumes=[100.0] * 61 + [200.0, 100.0],
        )

        with patch(
            "research.entry_signals.detect_vcp",
            side_effect=_accepted_after_sixty,
        ):
            rows = build_entry_signal_rows(history)

        self.assertEqual(
            [row["time"] for row in rows if row["strict_vcp_start"]],
            [history.index[59].date().isoformat()],
        )
        first_seen = rows[59]
        self.assertFalse(first_seen["vcp_breakout_price_confirmed"])
        breakout = rows[61]
        self.assertTrue(breakout["vcp_breakout_price_confirmed"])
        self.assertTrue(breakout["vcp_breakout_volume_confirmed"])
        self.assertTrue(breakout["vcp_breakout_buy_zone_confirmed"])
        self.assertTrue(breakout["vcp_breakout_confirmed"])
        self.assertEqual(breakout["vcp_breakout_pivot"], 10.0)
        self.assertAlmostEqual(breakout["vcp_breakout_volume_ratio"], 2.0)
        self.assertAlmostEqual(breakout["vcp_breakout_pct_over_pivot"], 1.0)

    def test_crossing_without_volume_is_not_confirmed(self):
        history = make_ohlcv(
            [9.0] * 60 + [9.5, 10.1],
            volumes=[100.0] * 62,
        )

        with patch(
            "research.entry_signals.detect_vcp",
            side_effect=_accepted_after_sixty,
        ):
            result = build_entry_signal_rows(history)[-1]

        self.assertTrue(result["vcp_breakout_price_confirmed"])
        self.assertFalse(result["vcp_breakout_volume_confirmed"])
        self.assertFalse(result["vcp_breakout_confirmed"])
        self.assertEqual(
            result["vcp_breakout_reject_reason"],
            "insufficient_breakout_volume",
        )

    def test_crossing_more_than_five_percent_above_pivot_is_extended(self):
        history = make_ohlcv(
            [9.0] * 60 + [9.5, 10.6],
            volumes=[100.0] * 61 + [200.0],
        )

        with patch(
            "research.entry_signals.detect_vcp",
            side_effect=_accepted_after_sixty,
        ):
            result = build_entry_signal_rows(history)[-1]

        self.assertTrue(result["vcp_breakout_price_confirmed"])
        self.assertTrue(result["vcp_breakout_volume_confirmed"])
        self.assertFalse(result["vcp_breakout_buy_zone_confirmed"])
        self.assertFalse(result["vcp_breakout_confirmed"])
        self.assertEqual(
            result["vcp_breakout_reject_reason"],
            "extended_above_buy_zone",
        )

    def test_appending_future_rows_never_changes_existing_results(self):
        prefix = textbook_vcp_fixture()
        future = make_ohlcv(
            np.linspace(prefix["Close"].iloc[-1], 105.0, 8),
            start=str((prefix.index[-1] + pd.offsets.BDay()).date()),
        )
        full = pd.concat([prefix, future])

        before = build_entry_signal_rows(prefix)
        after = build_entry_signal_rows(full)

        self.assertEqual(before, after[: len(before)])


if __name__ == "__main__":
    unittest.main()
