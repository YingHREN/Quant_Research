from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from web.factors.registry import FactorRegistry
from web.services.universe import UniverseSnapshotService


def _history(end="2026-07-21", periods=260):
    index = pd.bdate_range(end=end, periods=periods)
    close = np.linspace(100.0, 140.0, periods)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1_000_000, 1_200_000, periods),
        },
        index=index,
    )


class NumericFactor:
    label = "Numeric"
    group = "test"
    description = "Fixture"
    methodology = "Fixture"
    overview = True
    version = "test-v1"

    def __init__(self, key, direction, value):
        self.key = key
        self.direction = direction
        self.value = value

    def compute(self, context):
        return self.value

    def format(self, value):
        return str(value)


class FakeRepository:
    def __init__(self):
        self.latest_date = "2026-07-21"
        self.fail = False
        self.snapshot_calls = 0
        self.histories = {"AAA": _history(), "SPY": _history()}

    def freshness(self):
        return {"latest_date": self.latest_date, "by_date": []}

    def list_summaries(self):
        self.snapshot_calls += 1
        if self.fail:
            raise RuntimeError("snapshot failed")
        return [
            SimpleNamespace(
                ticker=ticker,
                latest_date=history.index[-1].date().isoformat(),
                lag_days=0,
                inactive=False,
            )
            for ticker, history in self.histories.items()
        ]

    def load_universe_histories(self, asof=None):
        return {
            ticker: history.loc[history.index <= asof].copy()
            for ticker, history in self.histories.items()
        }


def _registry():
    return FactorRegistry(
        [
            NumericFactor("mom_12_1", "higher", 1.0),
            NumericFactor("realized_vol_63", "lower", 0.2),
        ]
    )


class UniverseSnapshotServiceTest(unittest.TestCase):
    def test_cache_is_revision_scoped_bounded_and_returns_copies(self):
        repository = FakeRepository()
        revision = [3]
        service = UniverseSnapshotService(
            repository,
            _registry(),
            revision_getter=lambda: revision[0],
            max_cache_size=2,
        )

        first = service.build()
        first["tickers"][0]["ticker"] = "MUTATED"
        second = service.build()

        self.assertEqual(repository.snapshot_calls, 1)
        self.assertNotEqual(second["tickers"][0]["ticker"], "MUTATED")

        revision[0] = 4
        service.build()
        revision[0] = 5
        service.build()

        self.assertEqual(repository.snapshot_calls, 3)
        self.assertLessEqual(service.cache_size, 2)

    def test_failed_build_is_not_cached(self):
        repository = FakeRepository()
        service = UniverseSnapshotService(repository, _registry())
        repository.fail = True

        with self.assertRaises(RuntimeError):
            service.build()

        repository.fail = False
        payload = service.build()

        self.assertEqual(payload["asof"], "2026-07-21")
        self.assertEqual(repository.snapshot_calls, 2)
        self.assertEqual(service.cache_size, 1)


if __name__ == "__main__":
    unittest.main()
