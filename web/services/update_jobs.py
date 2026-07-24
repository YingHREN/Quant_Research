"""Single-worker, resumable price updates for the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
from urllib.error import HTTPError


logger = logging.getLogger(__name__)


class UpdateAlreadyRunning(RuntimeError):
    """Raised when a second update is requested while the worker is active."""


class RateLimited(RuntimeError):
    """Raised when the price provider returns HTTP 429."""


@dataclass(frozen=True)
class JobSnapshot:
    state: str
    started_at: datetime | None
    finished_at: datetime | None
    total: int
    completed: int
    updated: int
    current_ticker: str | None
    error: str | None
    resumable: bool

    def to_dict(self):
        return {
            "state": self.state,
            "started_at": _iso_timestamp(self.started_at),
            "finished_at": _iso_timestamp(self.finished_at),
            "total": self.total,
            "completed": self.completed,
            "updated": self.updated,
            "current_ticker": self.current_ticker,
            "error": self.error,
            "resumable": self.resumable,
        }


def _iso_timestamp(value):
    return None if value is None else value.isoformat()


class PriceProvider:
    """Production Tiingo adapter that fetches prices without fundamentals."""

    def fetch_history(self, ticker):
        from data.fetch import _tiingo_history

        try:
            return _tiingo_history(ticker)
        except HTTPError as error:
            if error.code == 429:
                raise RateLimited("rate_limited") from error
            raise


class UpdateJobManager:
    """Run at most one price update and preserve its pending ticker suffix."""

    _ALLOWED_TRANSITIONS = {
        "idle": frozenset({"running"}),
        "running": frozenset({"completed", "partial", "rate_limited", "failed"}),
        "completed": frozenset({"running"}),
        "partial": frozenset({"running"}),
        "rate_limited": frozenset({"running"}),
        "failed": frozenset({"running"}),
    }

    def __init__(
        self,
        repository,
        provider,
        on_success=None,
        reference_tickers=(),
    ):
        if on_success is not None and not callable(on_success):
            raise TypeError("on_success must be callable")
        checked_references = tuple(
            str(value).strip().upper() for value in reference_tickers
        )
        if (
            any(not value for value in checked_references)
            or len(set(checked_references)) != len(checked_references)
        ):
            raise ValueError(
                "reference_tickers must be unique non-empty symbols"
            )
        self._repository = repository
        self._provider = provider
        self._on_success = on_success
        self._reference_tickers = checked_references
        self._lock = threading.Lock()
        self._thread = None
        self._state = "idle"
        self._started_at = None
        self._finished_at = None
        self._total = 0
        self._completed = 0
        self._updated = 0
        self._current_ticker = None
        self._error = None
        self._resumable = False
        self._remaining_tickers = None
        self._had_errors = False
        self._run_updated_start = 0

    def start(self):
        """Start one daemon worker and return its initial running snapshot."""
        with self._lock:
            self._begin_locked()
            worker = threading.Thread(
                target=self._run,
                name="dashboard-price-update",
                daemon=True,
            )
            self._thread = worker
            snapshot = self._snapshot_locked()
        try:
            worker.start()
        except Exception:
            logger.exception("Unable to start dashboard price-update worker")
            with self._lock:
                self._finish_locked("failed", "provider_error", resumable=False)
            raise
        return snapshot

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def run_synchronously_for_test(self):
        """Exercise the same worker state machine without scheduling a thread."""
        with self._lock:
            self._begin_locked()
        self._run()
        return self.snapshot()

    def _begin_locked(self):
        if self._state == "running" or (
            self._thread is not None and self._thread.is_alive()
        ):
            raise UpdateAlreadyRunning("An update is already running")

        resume = self._resumable and bool(self._remaining_tickers)
        if not resume:
            self._started_at = datetime.now(timezone.utc)
            self._total = 0
            self._completed = 0
            self._updated = 0
            self._current_ticker = None
            self._remaining_tickers = None
            self._had_errors = False
        else:
            self._current_ticker = self._remaining_tickers[0]

        self._finished_at = None
        self._error = None
        self._resumable = False
        self._run_updated_start = self._updated
        self._transition_locked("running")

    def _run(self):
        try:
            self._load_tickers_if_needed()
            while True:
                with self._lock:
                    if not self._remaining_tickers:
                        state = "partial" if self._had_errors else "completed"
                        error = "provider_error" if self._had_errors else None
                        self._current_ticker = None
                        break
                    ticker = self._remaining_tickers[0]
                    self._current_ticker = ticker

                try:
                    frame = self._provider.fetch_history(ticker)
                    self._repository.upsert_history(ticker, frame)
                except RateLimited:
                    self._publish_terminal(
                        "rate_limited", "rate_limited", resumable=True
                    )
                    return
                except Exception:
                    logger.exception("Price update failed for %s", ticker)
                    with self._lock:
                        self._had_errors = True
                        self._completed += 1
                        self._remaining_tickers.pop(0)
                    continue

                with self._lock:
                    self._updated += 1
                    self._completed += 1
                    self._remaining_tickers.pop(0)
            self._publish_terminal(state, error, resumable=False)
        except Exception:
            logger.exception("Dashboard price-update worker failed")
            self._publish_terminal(
                "failed",
                "provider_error",
                resumable=bool(self._remaining_tickers),
            )

    def _publish_terminal(self, state, error, resumable):
        """Invalidate after this run's writes, then expose its terminal state."""
        with self._lock:
            wrote_prices = self._updated > self._run_updated_start
        if wrote_prices and self._on_success is not None:
            try:
                self._on_success()
            except Exception:
                logger.exception("Post-write cache invalidation callback failed")
                state = "failed"
                error = "cache_invalidation_error"
                resumable = False
        with self._lock:
            self._finish_locked(state, error, resumable)

    def _load_tickers_if_needed(self):
        with self._lock:
            if self._remaining_tickers is not None:
                return

        summaries = self._repository.list_summaries()
        active_tickers = [
            summary.ticker
            for summary in summaries
            if not getattr(summary, "inactive", False)
        ]
        ordered_tickers = tuple(
            dict.fromkeys((*active_tickers, *self._reference_tickers))
        )
        with self._lock:
            self._remaining_tickers = list(ordered_tickers)
            self._total = len(ordered_tickers)

    def _finish_locked(self, state, error, resumable):
        self._transition_locked(state)
        self._finished_at = datetime.now(timezone.utc)
        self._error = error
        self._resumable = resumable

    def _transition_locked(self, state):
        if state not in self._ALLOWED_TRANSITIONS[self._state]:
            raise RuntimeError(f"Invalid update transition: {self._state} -> {state}")
        self._state = state

    def _snapshot_locked(self):
        return JobSnapshot(
            state=self._state,
            started_at=self._started_at,
            finished_at=self._finished_at,
            total=self._total,
            completed=self._completed,
            updated=self._updated,
            current_ticker=self._current_ticker,
            error=self._error,
            resumable=self._resumable,
        )
