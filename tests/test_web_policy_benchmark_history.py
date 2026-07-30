import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research.expanded_market_data import (
    ExpandedMarketDataRepository,
    ExpandedMarketDataUnavailable,
)
from web.services.market_data import (
    MarketDataRepository,
    MarketDataUnavailable,
    UnknownTicker,
)
from web.services.policy_benchmark_history import (
    PolicyBenchmarkHistoryService,
)


def history(values=(100.0, 110.0, 121.0), dates=None):
    return pd.DataFrame(
        {"Close": values},
        index=pd.to_datetime(
            dates
            or ("2020-01-02", "2020-06-30", "2021-01-04")
        ),
    )


class ResearchRepositoryStub:
    def __init__(self, histories=None, error=None, token=("research", 1)):
        self.histories = histories or {}
        self.error = error
        self.token = token
        self.calls = []

    def load_universe_histories(self, *, asof=None, tickers=None):
        self.calls.append((asof, tickers))
        if self.error is not None:
            raise self.error
        return {
            ticker: frame.copy()
            for ticker, frame in self.histories.items()
        }

    def cache_token(self):
        return self.token


class PrimaryRepositoryStub:
    def __init__(self, histories=None, error=None):
        self.histories = histories or {}
        self.error = error
        self.calls = []

    def load_history(self, ticker, asof=None):
        self.calls.append((ticker, asof))
        if self.error is not None:
            raise self.error
        if ticker not in self.histories:
            raise UnknownTicker("missing")
        return self.histories[ticker].copy()


class PolicyBenchmarkHistoryServiceTest(unittest.TestCase):
    def test_research_adjusted_history_is_preferred_and_cut_off(self):
        research = ResearchRepositoryStub(
            {
                "SPY": history(
                    (100.0, 110.0, 999.0),
                    dates=(
                        "2020-01-02",
                        "2020-06-30",
                        "2027-01-04",
                    ),
                )
            }
        )
        primary = PrimaryRepositoryStub({"SPY": history()})
        service = PolicyBenchmarkHistoryService(
            primary,
            benchmark_repository=research,
            revision_getter=lambda: 7,
        )

        payload = service.build(
            asof="2026-07-29",
            benchmark="spy",
        )

        self.assertEqual(
            payload["artifact_key"],
            "policy_benchmark_history_v1",
        )
        self.assertEqual(payload["benchmark"], "SPY")
        self.assertEqual(payload["source"], "research_adjusted")
        self.assertEqual(
            [row["time"] for row in payload["rows"]],
            ["2020-01-02", "2020-06-30"],
        )
        self.assertEqual(payload["rows"][0]["normalized"], 100.0)
        self.assertEqual(payload["rows"][1]["normalized"], 110.0)
        self.assertEqual(primary.calls, [])
        self.assertEqual(payload["lifecycle"], "research")
        self.assertEqual(payload["decision_permission"], "advisory")
        self.assertEqual(payload["online_authority"], "none")
        self.assertTrue(payload["point_in_time"])
        self.assertTrue(payload["historical_description_only"])

    def test_primary_history_is_used_when_research_is_unavailable(self):
        research = ResearchRepositoryStub(
            error=ExpandedMarketDataUnavailable("missing")
        )
        primary = PrimaryRepositoryStub({"QQQ": history()})
        service = PolicyBenchmarkHistoryService(
            primary,
            benchmark_repository=research,
        )

        payload = service.build("2026-07-29", "QQQ")

        self.assertEqual(payload["source"], "primary_adjusted")
        self.assertEqual(len(payload["rows"]), 3)
        self.assertEqual(primary.calls, [("QQQ", "2026-07-29")])

    def test_missing_history_is_typed_and_never_filled_with_zero(self):
        service = PolicyBenchmarkHistoryService(
            PrimaryRepositoryStub(error=MarketDataUnavailable()),
            benchmark_repository=ResearchRepositoryStub(),
        )

        payload = service.build("2026-07-29", "SPY")

        self.assertEqual(payload["rows"], [])
        self.assertEqual(
            payload["unavailable_reason"],
            "benchmark_history_unavailable",
        )
        self.assertIsNone(payload["first_date"])
        self.assertIsNone(payload["last_date"])

    def test_only_spy_and_qqq_are_supported(self):
        service = PolicyBenchmarkHistoryService(
            PrimaryRepositoryStub(),
        )

        for value in ("XLK", "", None, 5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "SPY or QQQ",
                ):
                    service.build("2026-07-29", value)

    def test_missing_databases_are_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            primary_path = Path(directory) / "missing-primary.db"
            research_path = Path(directory) / "missing-research.db"
            service = PolicyBenchmarkHistoryService(
                MarketDataRepository(primary_path),
                benchmark_repository=ExpandedMarketDataRepository(
                    research_path
                ),
            )

            payload = service.build("2026-07-29", "SPY")

            self.assertEqual(
                payload["unavailable_reason"],
                "benchmark_history_unavailable",
            )
            self.assertFalse(primary_path.exists())
            self.assertFalse(research_path.exists())

    def test_cache_returns_fresh_copy_and_uses_bounded_capacity(self):
        research = ResearchRepositoryStub({"SPY": history(), "QQQ": history()})
        service = PolicyBenchmarkHistoryService(
            PrimaryRepositoryStub(),
            benchmark_repository=research,
            max_cache_size=1,
        )

        first = service.build("2026-07-29", "SPY")
        first["rows"][0]["close"] = -1
        second = service.build("2026-07-29", "SPY")
        service.build("2026-07-29", "QQQ")

        self.assertEqual(second["rows"][0]["close"], 100.0)
        self.assertEqual(service.cache_size, 1)
        self.assertEqual(len(research.calls), 2)

    def test_cache_size_must_be_a_positive_integer(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    PolicyBenchmarkHistoryService(
                        PrimaryRepositoryStub(),
                        max_cache_size=value,
                    )


if __name__ == "__main__":
    unittest.main()
