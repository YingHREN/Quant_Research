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


class FakeClassificationService:
    def __init__(self):
        self.calls = []

    def build(self, tickers):
        self.calls.append(tuple(tickers))
        return {
            "status": "available",
            "asof": "2026-07-24",
            "research_universe_count": 1014,
            "sector_counts": {
                "sec": {"technology": 237},
                "market_behavior": {"technology": 154},
            },
            "by_ticker": {
                ticker: {
                    "state": "agree" if ticker == "AAA" else "unclassified",
                    "sec": (
                        {
                            "sector_key": "technology",
                            "confidence": 1.0,
                            "source": "sec",
                            "rule_version": "sec_sic_v1",
                            "asof": "2026-07-24",
                        }
                        if ticker == "AAA"
                        else None
                    ),
                    "market_behavior": (
                        {
                            "sector_key": "technology",
                            "benchmark_ticker": "XLK",
                            "confidence": 0.8,
                            "source": "price_returns",
                            "rule_version": "market_behavior_v1",
                            "asof": "2026-07-24",
                        }
                        if ticker == "AAA"
                        else None
                    ),
                }
                for ticker in tickers
            },
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

    def test_build_merges_research_classifications_without_loading_prices(self):
        repository = FakeRepository()
        classifications = FakeClassificationService()
        service = UniverseSnapshotService(
            repository,
            _registry(),
            classification_service=classifications,
        )

        payload = service.build()

        self.assertEqual(payload["classification_summary"]["status"], "available")
        self.assertEqual(
            payload["classification_summary"]["research_universe_count"],
            1014,
        )
        by_ticker = {row["ticker"]: row for row in payload["tickers"]}
        self.assertEqual(
            by_ticker["AAA"]["sector_classification"]["state"],
            "agree",
        )
        self.assertEqual(
            by_ticker["AAA"]["sector_classification"]["market_behavior"][
                "benchmark_ticker"
            ],
            "XLK",
        )
        self.assertEqual(len(classifications.calls), 1)
        self.assertEqual(
            set(classifications.calls[0]),
            set(repository.histories),
        )


if __name__ == "__main__":
    unittest.main()
