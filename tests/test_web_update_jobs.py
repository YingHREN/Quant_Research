from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError

import pandas as pd

from web.services.update_jobs import (
    PriceProvider,
    RateLimited,
    UpdateAlreadyRunning,
    UpdateJobManager,
)


def history(close=10.0):
    return pd.DataFrame(
        {
            "Open": [close - 0.5],
            "High": [close + 0.5],
            "Low": [close - 1.0],
            "Close": [close],
            "Volume": [1_000.0],
        },
        index=pd.DatetimeIndex(["2026-07-21"], name="Date"),
    )


class FakeRepository:
    def __init__(self, tickers=("AAA", "BBB"), inactive=()):
        inactive = set(inactive)
        self.summaries = [
            SimpleNamespace(ticker=ticker, inactive=ticker in inactive)
            for ticker in tickers
        ]
        self.upserts = []

    def list_summaries(self):
        return list(self.summaries)

    def upsert_history(self, ticker, frame):
        self.upserts.append((ticker, frame.copy()))


class RejectingInvalidRepository(FakeRepository):
    def upsert_history(self, ticker, frame):
        raise ValueError("Provider history is invalid")


class FakeProvider:
    def __init__(self, outcomes):
        self.outcomes = {
            ticker: list(value) if isinstance(value, list) else [value]
            for ticker, value in outcomes.items()
        }
        self.calls = []

    def fetch_history(self, ticker):
        self.calls.append(ticker)
        outcome = self.outcomes[ticker].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class BlockingProvider:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def fetch_history(self, ticker):
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test provider timed out")
        return history()


class SQLiteRecordingRepository(FakeRepository):
    """A test repository whose individual upserts are real SQLite commits."""

    def __init__(self, path):
        super().__init__()
        self.path = path
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE prices (ticker TEXT, date TEXT, close REAL, "
                "PRIMARY KEY (ticker, date))"
            )

    def upsert_history(self, ticker, frame):
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO prices VALUES (?, ?, ?)",
                [
                    (ticker, str(index.date()), float(row["Close"]))
                    for index, row in frame.iterrows()
                ],
            )
            connection.commit()


def wait_until_terminal(manager, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot()
        if snapshot.state != "running":
            return snapshot
        time.sleep(0.005)
    raise AssertionError("update worker did not reach a terminal state")


class UpdateJobManagerTest(unittest.TestCase):
    def test_completed_job_updates_active_tickers_only(self):
        repository = FakeRepository(("AAA", "OLD", "BBB"), inactive=("OLD",))
        provider = FakeProvider({"AAA": history(10), "BBB": history(20)})
        manager = UpdateJobManager(repository, provider)

        snapshot = manager.run_synchronously_for_test().to_dict()

        self.assertEqual(snapshot["state"], "completed")
        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(snapshot["completed"], 2)
        self.assertEqual(snapshot["updated"], 2)
        self.assertIsNone(snapshot["current_ticker"])
        self.assertIsNone(snapshot["error"])
        self.assertFalse(snapshot["resumable"])
        self.assertIsNotNone(snapshot["started_at"])
        self.assertIsNotNone(snapshot["finished_at"])
        self.assertEqual(provider.calls, ["AAA", "BBB"])
        self.assertEqual([ticker for ticker, _ in repository.upserts], ["AAA", "BBB"])

    def test_rejects_concurrent_start(self):
        provider = BlockingProvider()
        manager = UpdateJobManager(FakeRepository(("AAA",)), provider)
        started = manager.start()
        self.assertEqual(started.state, "running")
        self.assertTrue(provider.entered.wait(timeout=1))

        with self.assertRaises(UpdateAlreadyRunning):
            manager.start()

        provider.release.set()
        self.assertEqual(wait_until_terminal(manager).state, "completed")

    def test_rejects_restart_until_terminal_worker_thread_has_exited(self):
        provider = FakeProvider({"AAA": [history(10), history(11)]})
        manager = UpdateJobManager(FakeRepository(("AAA",)), provider)
        run_worker = manager._run
        terminal_published = threading.Event()
        allow_thread_exit = threading.Event()

        def publish_terminal_then_wait():
            run_worker()
            terminal_published.set()
            allow_thread_exit.wait(timeout=2)

        manager._run = publish_terminal_then_wait
        manager.start()
        first_worker = manager._thread
        self.assertTrue(terminal_published.wait(timeout=1))
        self.assertEqual(manager.snapshot().state, "completed")
        self.assertTrue(first_worker.is_alive())

        try:
            with self.assertRaises(UpdateAlreadyRunning):
                manager.start()
        finally:
            allow_thread_exit.set()
            first_worker.join(timeout=1)
            if manager._thread is not first_worker:
                manager._thread.join(timeout=1)

        self.assertFalse(first_worker.is_alive())
        manager.start()
        self.assertEqual(wait_until_terminal(manager).state, "completed")

    def test_thread_start_failure_is_failed_and_not_resumable(self):
        manager = UpdateJobManager(FakeRepository(("AAA",)), FakeProvider({}))

        with mock.patch.object(
            threading.Thread, "start", side_effect=RuntimeError("cannot start")
        ):
            with self.assertLogs("web.services.update_jobs", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "cannot start"):
                    manager.start()

        snapshot = manager.snapshot().to_dict()
        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(snapshot["error"], "provider_error")
        self.assertFalse(snapshot["resumable"])

    def test_rate_limit_preserves_committed_progress_and_is_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteRecordingRepository(Path(tmp) / "prices.db")
            provider = FakeProvider(
                {
                    "AAA": history(10),
                    "BBB": [RateLimited("429"), history(20)],
                }
            )
            manager = UpdateJobManager(repository, provider)

            first = manager.run_synchronously_for_test().to_dict()
            with sqlite3.connect(repository.path) as connection:
                committed = connection.execute(
                    "SELECT ticker FROM prices ORDER BY ticker"
                ).fetchall()

            self.assertEqual(first["state"], "rate_limited")
            self.assertEqual(first["completed"], 1)
            self.assertEqual(first["updated"], 1)
            self.assertEqual(first["current_ticker"], "BBB")
            self.assertEqual(first["error"], "rate_limited")
            self.assertTrue(first["resumable"])
            self.assertEqual(committed, [("AAA",)])

            resumed = manager.run_synchronously_for_test().to_dict()

        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(resumed["completed"], 2)
        self.assertEqual(resumed["updated"], 2)
        self.assertFalse(resumed["resumable"])
        self.assertEqual(provider.calls, ["AAA", "BBB", "BBB"])

    def test_provider_error_is_redacted_and_other_tickers_continue(self):
        secret = "/Users/alice/env.sh?token=super-secret"
        repository = FakeRepository(("AAA", "BBB"))
        provider = FakeProvider({"AAA": RuntimeError(secret), "BBB": history()})
        manager = UpdateJobManager(repository, provider)

        with self.assertLogs("web.services.update_jobs", level="ERROR") as logs:
            snapshot = manager.run_synchronously_for_test().to_dict()

        self.assertEqual(snapshot["state"], "partial")
        self.assertEqual(snapshot["completed"], 2)
        self.assertEqual(snapshot["updated"], 1)
        self.assertEqual(snapshot["error"], "provider_error")
        self.assertNotIn(secret, str(snapshot))
        self.assertIn(secret, "\n".join(logs.output))
        self.assertEqual([ticker for ticker, _ in repository.upserts], ["BBB"])

    def test_text_containing_429_is_not_treated_as_http_rate_limit(self):
        manager = UpdateJobManager(
            FakeRepository(("AAA",)),
            FakeProvider({"AAA": RuntimeError("account 4299 unavailable")}),
        )

        with self.assertLogs("web.services.update_jobs", level="ERROR"):
            snapshot = manager.run_synchronously_for_test().to_dict()

        self.assertEqual(snapshot["state"], "partial")
        self.assertEqual(snapshot["error"], "provider_error")
        self.assertFalse(snapshot["resumable"])

    def test_invalid_provider_bars_are_a_partial_failure_not_an_update(self):
        repository = RejectingInvalidRepository(("AAA",))
        manager = UpdateJobManager(
            repository,
            FakeProvider({"AAA": history(close=-5.0)}),
        )

        with self.assertLogs("web.services.update_jobs", level="ERROR"):
            snapshot = manager.run_synchronously_for_test().to_dict()

        self.assertEqual(snapshot["state"], "partial")
        self.assertEqual(snapshot["completed"], 1)
        self.assertEqual(snapshot["updated"], 0)
        self.assertEqual(snapshot["error"], "provider_error")
        self.assertEqual(repository.upserts, [])


class PriceProviderTest(unittest.TestCase):
    def test_fetch_history_calls_tiingo_history_directly(self):
        expected = history()
        with mock.patch("data.fetch._tiingo_history", return_value=expected) as tiingo:
            with mock.patch("data.fetch.fetch", side_effect=AssertionError("must not fetch")):
                actual = PriceProvider().fetch_history("AAA")

        self.assertIs(actual, expected)
        tiingo.assert_called_once_with("AAA")

    def test_converts_exact_http_429_to_rate_limited(self):
        response = HTTPError(
            "https://api.tiingo.com/tiingo/daily/AAA/prices",
            429,
            "Too Many Requests",
            hdrs=None,
            fp=None,
        )
        with mock.patch("data.fetch._tiingo_history", side_effect=response):
            with self.assertRaises(RateLimited):
                PriceProvider().fetch_history("AAA")

    def test_non_429_http_error_is_not_misclassified(self):
        response = HTTPError(
            "https://api.tiingo.com/tiingo/daily/AAA/prices",
            503,
            "Service Unavailable",
            hdrs=None,
            fp=None,
        )
        with mock.patch("data.fetch._tiingo_history", side_effect=response):
            with self.assertRaises(HTTPError) as context:
                PriceProvider().fetch_history("AAA")

        self.assertEqual(context.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
