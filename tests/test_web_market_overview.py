import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from web.services.market_data import (
    MarketDataRepository,
    MarketDataUnavailable,
)
from web.services.market_overview import MarketOverviewService


def history(periods=260, end="2026-07-23", slope=0.2):
    index = pd.bdate_range(end=end, periods=periods)
    close = 100.0 + np.arange(periods) * slope
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(periods, 1_000_000.0),
        },
        index=index,
    )


def fixture_histories():
    return {
        ticker: history(slope=slope)
        for ticker, slope in (
            ("QQQ", 0.2),
            ("SPY", 0.15),
            ("SOXX", 0.25),
            ("SMH", 0.3),
            ("AMD", 0.35),
        )
    }


class FakeRepository:
    def __init__(self, histories):
        self.histories = histories
        self.calls = []

    def load_market_overview_snapshot(self, asof=None):
        self.calls.append(asof)
        cutoff = max(frame.index[-1] for frame in self.histories.values())
        if asof is not None:
            cutoff = min(cutoff, pd.Timestamp(asof))
        return SimpleNamespace(
            observation_date=cutoff.date().isoformat(),
            histories={
                key: value.loc[value.index <= cutoff].copy()
                for key, value in self.histories.items()
            },
        )


class MarketOverviewServiceTest(unittest.TestCase):
    def test_build_reads_one_snapshot_and_returns_daily_proxy(self):
        repository = FakeRepository(fixture_histories())
        service = MarketOverviewService(repository)

        payload = service.build(
            asof="2026-07-23",
            horizon=5,
            sector="semiconductor",
        )

        self.assertEqual(repository.calls, ["2026-07-23"])
        self.assertEqual(payload["asof"], "2026-07-23")
        self.assertEqual(payload["evidence_tier"], "daily_proxy")
        self.assertEqual(payload["requested_horizon"], 5)

    def test_empty_snapshot_returns_typed_unavailable_payload(self):
        repository = FakeRepository(fixture_histories())
        repository.load_market_overview_snapshot = (
            lambda asof=None: SimpleNamespace(
                observation_date=None,
                histories={},
            )
        )
        service = MarketOverviewService(repository)

        payload = service.build()

        self.assertIsNone(payload["asof"])
        self.assertEqual(
            payload["market_posture"]["unavailable_reason"],
            "market_data_unavailable",
        )
        self.assertEqual(payload["intraday"]["state"], "unavailable")


class MarketOverviewRepositoryTest(unittest.TestCase):
    def test_snapshot_uses_one_normalized_cutoff_for_every_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.db"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE prices (
                        ticker TEXT,
                        date TEXT,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL,
                        volume REAL,
                        PRIMARY KEY (ticker, date)
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        ("AAA", "2026-07-22", 10, 11, 9, 10.5, 100),
                        ("AAA", "2026-07-23", 11, 12, 10, 11.5, 110),
                        ("QQQ", "2026-07-22", 20, 21, 19, 20.5, 200),
                        ("QQQ", "2026-07-24", 21, 22, 20, 21.5, 210),
                    ),
                )

            snapshot = MarketDataRepository(
                path
            ).load_market_overview_snapshot("2026-07-23 16:20:00")

            self.assertEqual(snapshot.observation_date, "2026-07-23")
            self.assertEqual(set(snapshot.histories), {"AAA", "QQQ"})
            self.assertTrue(
                all(
                    frame.index.max() <= pd.Timestamp("2026-07-23")
                    for frame in snapshot.histories.values()
                )
            )

    def test_missing_database_is_not_created_by_market_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"

            with self.assertRaises(MarketDataUnavailable):
                MarketDataRepository(path).load_market_overview_snapshot()

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
