from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from web.factors.registry import FactorRegistry
from web.services.universe import UniverseSnapshotService, build_structure_summary


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

    def build(self, tickers, asof=None):
        self.calls.append((tuple(tickers), asof))
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


def _sndk_assignment():
    return {
        "state": "assigned",
        "ticker": "SNDK",
        "rule_version": "security_group_overrides_v1",
        "effective_from": "2025-02-24",
        "effective_to": "9999-12-31",
        "observed_at": "2026-07-21",
        "sector_key": "technology",
        "sector_benchmark": "XLK",
        "theme_keys": ["semiconductor"],
        "theme_benchmarks": {"semiconductor": ["SOXX", "SMH"]},
        "primary_model_group": "semiconductor",
        "classification_state": "classified",
        "source": "manual_override",
        "confidence": 1.0,
        "override_reason": "flash memory and storage semiconductor exposure",
    }


class FakeGroupingClassificationService(FakeClassificationService):
    def build(self, tickers, asof=None):
        payload = super().build(tickers, asof=asof)
        for ticker, classification in payload["by_ticker"].items():
            classification["group_assignment"] = (
                _sndk_assignment()
                if ticker == "SNDK"
                else {
                    "state": "missing",
                    "reason": "no_assignment_effective_at_asof",
                }
            )
        payload.update(
            {
                "group_assignment_status": "available",
                "group_assignment_asof": "2026-07-21",
                "group_assignment_revision": 41,
                "group_assignment_coverage": 0.5,
                "group_assignment_review_count": 0,
            }
        )
        return payload


class FakeGroupAssignmentRepository:
    def __init__(self):
        self.calls = []

    def build(self, tickers, asof=None):
        normalized = tuple(sorted(tickers))
        self.calls.append((normalized, asof))
        return {
            "status": "available",
            "asof": asof,
            "revision": 41,
            "coverage": 1.0 / len(normalized) if normalized else 1.0,
            "review_count": 0,
            "by_ticker": {
                ticker: (
                    _sndk_assignment()
                    if ticker == "SNDK"
                    else {
                        "state": "missing",
                        "reason": "no_assignment_effective_at_asof",
                    }
                )
                for ticker in normalized
            },
        }


class FakeRelativeStrengthService:
    def __init__(self):
        self.calls = []

    def build(self, tickers):
        self.calls.append(tuple(tickers))
        return {
            "status": "available",
            "asof": "2026-07-21",
            "sample_count": 1000,
            "model_version": "cross_sectional_rs_v1",
            "by_ticker": {
                ticker: {
                    "rs_rating": 91 if ticker == "AAA" else 60,
                    "rs_asof": "2026-07-21",
                    "rs_sample_count": 1000,
                    "rs_model_version": "cross_sectional_rs_v1",
                }
                for ticker in tickers
            },
        }


class FakeResearchUniverseRepository:
    def __init__(self, available=True):
        self.available = available
        self.calls = []
        self.histories = {
            "AAA": _history(),
            "BBB": _history(),
            "SPY": _history(),
        }

    def revision(self):
        return 17

    def snapshot(self, asof=None, sessions=260):
        self.calls.append((asof, sessions))
        if not self.available:
            return SimpleNamespace(
                status="unavailable",
                asof=asof,
                revision=17,
                members=(),
                histories={},
                reason="database_unavailable",
            )
        return SimpleNamespace(
            status="available",
            asof=asof,
            revision=17,
            members=tuple(
                SimpleNamespace(
                    ticker=ticker,
                    latest_date=history.index[-1].date().isoformat(),
                    stale=False,
                    name=f"{ticker} Inc.",
                    exchange="NASDAQ",
                )
                for ticker, history in self.histories.items()
            ),
            histories=self.histories,
            reason=None,
        )


def _registry():
    return FactorRegistry(
        [
            NumericFactor("mom_12_1", "higher", 1.0),
            NumericFactor("realized_vol_63", "lower", 0.2),
        ]
    )


class UniverseSnapshotServiceTest(unittest.TestCase):
    def test_structure_summary_exposes_filterable_vcp_states(self):
        history = _history()
        pattern = SimpleNamespace(
            accepted=True,
            stage="near_pivot",
            distance_to_pivot_pct=-2.5,
        )

        with (
            patch("web.services.universe.detect_vcp", return_value=pattern),
            patch(
                "web.services.universe.tight_platform",
                return_value={"is_platform": True},
            ),
        ):
            summary = build_structure_summary(history)

        self.assertEqual(
            summary,
            {
                "strict_vcp": True,
                "tight_platform": True,
                "near_pivot": True,
                "shape_state": "near_pivot",
            },
        )

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
        self.assertIn(payload["market_gate"]["state"], {"pass", "fail", "missing"})
        self.assertTrue(
            all(
                row["formal_candidate_state"] in {"pass", "fail", "missing"}
                for row in payload["tickers"]
            )
        )
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
            set(classifications.calls[0][0]),
            set(repository.histories),
        )
        self.assertEqual(classifications.calls[0][1], "2026-07-21")

    def test_build_exposes_the_repository_group_assignment_on_each_row(self):
        repository = FakeRepository()
        repository.histories = {
            "SNDK": _history(),
            "NBIS": _history(),
            "SPY": _history(),
        }
        assignments = FakeGroupAssignmentRepository()
        service = UniverseSnapshotService(
            repository,
            _registry(),
            classification_service=FakeGroupingClassificationService(),
            group_assignment_repository=assignments,
        )

        payload = service.build()

        by_ticker = {row["ticker"]: row for row in payload["tickers"]}
        self.assertEqual(by_ticker["SNDK"]["group_assignment"], _sndk_assignment())
        self.assertEqual(
            {
                key: by_ticker["SNDK"]["group_assignment"][key]
                for key in (
                    "sector_key",
                    "sector_benchmark",
                    "theme_keys",
                    "theme_benchmarks",
                    "primary_model_group",
                )
            },
            {
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": ["semiconductor"],
                "theme_benchmarks": {"semiconductor": ["SOXX", "SMH"]},
                "primary_model_group": "semiconductor",
            },
        )
        self.assertEqual(
            by_ticker["NBIS"]["group_assignment"],
            {
                "state": "missing",
                "reason": "no_assignment_effective_at_asof",
            },
        )
        self.assertNotIn(
            "group_assignment",
            by_ticker["SNDK"]["sector_classification"],
        )
        self.assertEqual(
            assignments.calls,
            [(("NBIS", "SNDK", "SPY"), "2026-07-21")],
        )

    def test_unavailable_classification_keeps_a_stable_assignment_reason(self):
        payload = UniverseSnapshotService(
            FakeRepository(),
            _registry(),
        ).build()

        self.assertTrue(
            all(
                row["group_assignment"]
                == {
                    "state": "missing",
                    "reason": "assignment_repository_unavailable",
                }
                for row in payload["tickers"]
            )
        )

    def test_build_merges_precomputed_relative_strength(self):
        repository = FakeRepository()
        relative_strength = FakeRelativeStrengthService()
        service = UniverseSnapshotService(
            repository,
            _registry(),
            relative_strength_service=relative_strength,
        )

        payload = service.build()

        self.assertEqual(
            payload["relative_strength_summary"],
            {
                "status": "available",
                "asof": "2026-07-21",
                "sample_count": 1000,
                "model_version": "cross_sectional_rs_v1",
            },
        )
        by_ticker = {row["ticker"]: row for row in payload["tickers"]}
        self.assertEqual(by_ticker["AAA"]["rs_rating"], 91)
        self.assertEqual(by_ticker["AAA"]["rs_asof"], "2026-07-21")
        self.assertEqual(
            by_ticker["AAA"]["rs_model_version"],
            "cross_sectional_rs_v1",
        )
        self.assertEqual(len(relative_strength.calls), 1)

    def test_missing_relative_strength_is_explicit(self):
        payload = UniverseSnapshotService(
            FakeRepository(),
            _registry(),
        ).build()

        self.assertEqual(
            payload["relative_strength_summary"]["status"],
            "unavailable",
        )
        self.assertTrue(
            all(row["rs_rating"] is None for row in payload["tickers"])
        )

    def test_build_merges_active_and_research_pools_without_duplicate_rows(self):
        repository = FakeRepository()
        research_repository = FakeResearchUniverseRepository()
        service = UniverseSnapshotService(
            repository,
            _registry(),
            research_universe_repository=research_repository,
        )

        payload = service.build()

        self.assertEqual(
            payload["pool_summary"],
            {"active_count": 2, "research_count": 3, "overlap_count": 2},
        )
        self.assertEqual(payload["research_pool_status"]["status"], "available")
        by_ticker = {row["ticker"]: row for row in payload["tickers"]}
        self.assertEqual(set(by_ticker), {"AAA", "BBB", "SPY"})
        self.assertEqual(
            by_ticker["AAA"]["pool_membership"],
            {"active": True, "research": True},
        )
        self.assertEqual(
            by_ticker["BBB"]["pool_membership"],
            {"active": False, "research": True},
        )
        self.assertEqual(by_ticker["BBB"]["shape_state"], "unavailable")
        self.assertIn(by_ticker["BBB"]["technical_gate"]["state"], {"pass", "fail"})
        self.assertEqual(research_repository.calls, [("2026-07-21", 260)])

    def test_unavailable_research_pool_preserves_active_rows(self):
        service = UniverseSnapshotService(
            FakeRepository(),
            _registry(),
            research_universe_repository=FakeResearchUniverseRepository(
                available=False
            ),
        )

        payload = service.build()

        self.assertEqual(payload["research_pool_status"]["status"], "unavailable")
        self.assertEqual(
            payload["pool_summary"],
            {"active_count": 2, "research_count": 0, "overlap_count": 0},
        )
        self.assertTrue(
            all(
                row["pool_membership"] == {"active": True, "research": False}
                for row in payload["tickers"]
            )
        )


if __name__ == "__main__":
    unittest.main()
