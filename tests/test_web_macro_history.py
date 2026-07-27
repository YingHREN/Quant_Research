import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

from web.services.macro_history import MacroHistoryService


def benchmark_history(periods=520, end="2026-07-23"):
    index = pd.bdate_range(end=end, periods=periods)
    close = 100.0 + np.arange(periods, dtype=float)
    return pd.DataFrame({"Close": close}, index=index)


class FakeRepository:
    def __init__(self, histories):
        self.histories = histories
        self.calls = []

    def load_market_overview_snapshot(self, asof=None):
        self.calls.append(asof)
        cutoff = pd.Timestamp(asof or "2026-07-23")
        scoped = {
            ticker: frame.loc[frame.index <= cutoff].copy()
            for ticker, frame in self.histories.items()
        }
        latest = max(
            (frame.index[-1] for frame in scoped.values() if not frame.empty),
            default=None,
        )
        return SimpleNamespace(
            observation_date=(
                latest.date().isoformat() if latest is not None else None
            ),
            histories=scoped,
        )


class FakeMacroRiskService:
    def __init__(self):
        self.calls = []

    def build_history(self, dates):
        self.calls.append(tuple(dates))
        return [
            {
                "time": date,
                "score": 25.0,
                "coverage": 1.0,
                "state": "low",
                "components": {
                    "rates": {"score": 50.0, "coverage": 1.0},
                    "inflation_energy": {"score": 0.0, "coverage": 1.0},
                    "credit_liquidity": {
                        "score": 0.0,
                        "coverage": 1.0,
                    },
                    "risk_aversion": {"score": 25.0, "coverage": 1.0},
                },
                "series": {
                    "DGS2": {
                        "value": 4.5,
                        "observation_date": date,
                        "available_at": f"{date}T18:00:00+00:00",
                        "series_ids": ["DGS2"],
                    }
                },
                "evidence": [],
                "unavailable_reason": None,
            }
            for date in dates
        ]

    def cache_token(self):
        return ("macro", 1)


class MacroHistoryServiceTest(unittest.TestCase):
    def test_builds_range_filtered_rows_with_normalized_benchmark(self):
        macro = FakeMacroRiskService()
        service = MacroHistoryService(
            FakeRepository(
                {
                    "SPY": benchmark_history(),
                    "QQQ": benchmark_history(),
                }
            ),
            macro,
        )

        payload = service.build(
            asof="2026-07-23",
            range_key="1y",
            benchmark="QQQ",
        )

        self.assertEqual(payload["asof"], "2026-07-23")
        self.assertEqual(payload["range"], "1y")
        self.assertEqual(payload["benchmark"], "QQQ")
        self.assertTrue(payload["point_in_time"])
        self.assertGreater(len(payload["rows"]), 200)
        self.assertGreaterEqual(payload["rows"][0]["time"], "2025-07-23")
        self.assertEqual(payload["rows"][0]["benchmark_normalized"], 100.0)
        self.assertGreater(
            payload["rows"][-1]["benchmark_normalized"],
            100.0,
        )
        self.assertEqual(
            macro.calls[0],
            tuple(row["time"] for row in payload["rows"]),
        )
        self.assertIn("DGS2", payload["series_catalog"])

    def test_rejects_unknown_range_and_benchmark(self):
        service = MacroHistoryService(
            FakeRepository({"SPY": benchmark_history()}),
            FakeMacroRiskService(),
        )

        with self.assertRaisesRegex(ValueError, "range"):
            service.build(range_key="10y")
        with self.assertRaisesRegex(ValueError, "benchmark"):
            service.build(benchmark="AMD")

    def test_missing_benchmark_returns_typed_unavailable_payload(self):
        service = MacroHistoryService(
            FakeRepository({}),
            FakeMacroRiskService(),
        )

        payload = service.build(benchmark="SPY")

        self.assertEqual(payload["rows"], [])
        self.assertEqual(
            payload["unavailable_reason"],
            "benchmark_history_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
