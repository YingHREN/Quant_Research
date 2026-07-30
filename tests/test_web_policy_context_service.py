import tempfile
import unittest
from pathlib import Path

from web.services.macro_store import MacroObservationStore
from web.services.policy_context import PolicyContextService


def observation(
    series_id,
    observation_date,
    value,
    available_at,
    *,
    realtime_start=None,
):
    realtime_start = realtime_start or observation_date
    return {
        "series_id": series_id,
        "observation_date": observation_date,
        "available_at": available_at,
        "value": value,
        "realtime_start": realtime_start,
        "realtime_end": "9999-12-31",
        "source": "FRED/ALFRED",
        "revision_policy": "initial_release_only",
    }


def policy_observations():
    rows = []
    for series_id, values in (
        ("DFEDTARL", (3.50, 3.50)),
        ("DFEDTARU", (3.75, 3.75)),
        ("WALCL", (6_500_000, 6_600_000)),
        ("WRESBAL", (3_000_000, 3_060_000)),
        ("DFII10", (1.60, 1.90)),
    ):
        rows.extend(
            (
                observation(
                    series_id,
                    "2026-04-01",
                    values[0],
                    "2026-04-02T12:00:00+00:00",
                ),
                observation(
                    series_id,
                    "2026-07-15",
                    values[1],
                    "2026-07-16T12:00:00+00:00",
                ),
            )
        )
    for series_id, values in (
        ("PCEPI", (119.0, 119.5, 121.0, 121.8)),
        ("PCEPILFE", (118.0, 118.6, 120.3, 121.0)),
    ):
        for date, value, available_at in zip(
            ("2025-03-01", "2025-06-01", "2026-03-01", "2026-06-01"),
            values,
            (
                "2025-04-30T12:30:00+00:00",
                "2025-07-31T12:30:00+00:00",
                "2026-04-30T12:30:00+00:00",
                "2026-06-30T12:30:00+00:00",
            ),
        ):
            rows.append(observation(series_id, date, value, available_at))
    return rows


class PolicyContextServiceTest(unittest.TestCase):
    def test_builds_policy_context_from_release_aware_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.db"
            store = MacroObservationStore(path)
            store.initialize()
            store.upsert(policy_observations())

            payload = PolicyContextService(path).build("2026-07-20")

            self.assertEqual(
                payload["state"],
                "rate_restrictive_liquidity_support",
            )
            self.assertEqual(
                payload["dimensions"]["policy_rate"]["lower"],
                3.5,
            )
            self.assertTrue(payload["point_in_time"])

    def test_missing_database_returns_typed_unavailable_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"

            payload = PolicyContextService(path).build("2026-07-20")

            self.assertEqual(payload["state"], "unavailable")
            self.assertEqual(
                payload["unavailable_reason"],
                "policy_data_unavailable",
            )
            self.assertFalse(path.exists())

    def test_store_change_updates_token_and_invalidates_cached_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.db"
            store = MacroObservationStore(path)
            store.initialize()
            store.upsert(policy_observations())
            service = PolicyContextService(path)
            first_token = service.cache_token()
            first = service.build("2026-07-20")

            store.upsert(
                [
                    observation(
                        "DFII10",
                        "2026-07-17",
                        2.20,
                        "2026-07-18T12:00:00+00:00",
                    )
                ]
            )
            second_token = service.cache_token()
            second = service.build("2026-07-20")

            self.assertNotEqual(second_token, first_token)
            self.assertNotEqual(
                second["dimensions"]["real_rate"]["value"],
                first["dimensions"]["real_rate"]["value"],
            )


if __name__ == "__main__":
    unittest.main()
