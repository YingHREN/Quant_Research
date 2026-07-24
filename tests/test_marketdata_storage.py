from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from marketdata.base import (
    ProviderCapabilities,
    QuoteEvent,
    TradeCancelEvent,
    TradeCorrectionEvent,
    TradeEvent,
)
from marketdata.storage import IntradayStore


UTC = timezone.utc
AT = datetime(2026, 7, 24, 14, 30, tzinfo=UTC)


def trade(sequence="1"):
    return TradeEvent(
        provider="alpaca", symbol="AMD", event_ts=AT, received_ts=AT,
        price=150.0, size=100.0, exchange="V", conditions=("@",),
        direction="buy", direction_source="quote_mid",
        source_sequence=sequence, session="regular",
    )


class IntradayStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "market.db"
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE prices (ticker TEXT)")
        self.store = IntradayStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_event_is_idempotent_and_prices_table_survives(self):
        self.assertTrue(self.store.write_event(trade()))
        self.assertFalse(self.store.write_event(trade()))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM intraday_trades").fetchone()[0]
            prices = connection.execute(
                "SELECT name FROM sqlite_master WHERE name='prices'"
            ).fetchone()
        self.assertEqual(count, 1)
        self.assertIsNotNone(prices)

    def test_status_reports_capability_and_subscription_coverage(self):
        capability = ProviderCapabilities(
            "alpaca", "iex", True, 30, False, False, True, False, False, None
        )
        self.store.record_capabilities(capability, AT)
        self.store.open_subscription("alpaca", ("SPY", "AMD"), AT)
        status = self.store.status()
        self.assertEqual(status["provider"], "alpaca")
        self.assertEqual(status["coverage"], "iex")
        self.assertEqual(status["subscribed_symbols"], ["AMD", "SPY"])

    def test_fallback_trade_sequence_distinguishes_metadata_and_is_idempotent(self):
        first = trade(sequence=None)
        different_metadata = replace(first, conditions=("@", "I"))
        self.assertTrue(self.store.write_event(first))
        self.assertFalse(self.store.write_event(first))
        self.assertTrue(self.store.write_event(different_metadata))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM intraday_trades").fetchone()[0]
        self.assertEqual(count, 2)

    def test_fallback_quote_sequence_distinguishes_size_and_is_idempotent(self):
        first = QuoteEvent(
            "alpaca", "AMD", AT, AT, 149.0, 10.0, 151.0, 12.0, None, "regular"
        )
        different_size = replace(first, bid_size=11.0)
        self.assertTrue(self.store.write_event(first))
        self.assertFalse(self.store.write_event(first))
        self.assertTrue(self.store.write_event(different_size))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM intraday_quotes").fetchone()[0]
        self.assertEqual(count, 2)

    def test_close_and_reopen_at_same_timestamp_preserves_active_interval(self):
        self.store.open_subscription("alpaca", ("AMD",), AT)
        self.store.close_subscription("alpaca", ("AMD",), AT)
        self.store.open_subscription("alpaca", ("AMD",), AT)
        with sqlite3.connect(self.path) as connection:
            intervals = connection.execute(
                "SELECT finished_at FROM subscription_intervals WHERE provider='alpaca' AND symbol='AMD'"
            ).fetchall()
        self.assertEqual(len(intervals), 2)
        self.assertEqual(sum(finished_at is None for (finished_at,) in intervals), 1)
        self.assertEqual(self.store.status()["subscribed_symbols"], ["AMD"])

    def test_lifecycle_timestamps_require_utc(self):
        capability = ProviderCapabilities(
            "alpaca", "iex", True, 30, False, False, True, False, False, None
        )
        naive = datetime(2026, 7, 24, 14, 30)
        non_utc = datetime(2026, 7, 24, 22, 30, tzinfo=timezone(timedelta(hours=8)))
        with self.assertRaises(ValueError):
            self.store.record_capabilities(capability, naive)
        with self.assertRaises(ValueError):
            self.store.open_subscription("alpaca", ("AMD",), non_utc)
        with self.assertRaises(ValueError):
            self.store.close_subscription("alpaca", ("AMD",), naive)

    def test_quotes_separated_only_by_800_nanoseconds_both_persist(self):
        first = QuoteEvent(
            "alpaca", "AMD", AT, AT, 149.0, 10.0, 151.0, 12.0, None,
            "regular", event_ts_ns=1784903400000000100,
        )
        second = replace(first, event_ts_ns=1784903400000000900)
        self.assertEqual(self.store.write_events((first, second)), 2)
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT event_ts_ns, trading_date FROM intraday_quotes "
                "ORDER BY event_ts_ns"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (1784903400000000100, "2026-07-24"),
                (1784903400000000900, "2026-07-24"),
            ],
        )

    def test_quote_size_unit_and_lot_provenance_are_persisted(self):
        quote = QuoteEvent(
            "alpaca", "AMD", AT, AT, 149.0, 300.0, 151.0, 400.0, None,
            "regular", event_ts_ns=1784903400000000000,
            size_unit="shares", lot_size=100,
        )
        self.assertTrue(self.store.write_event(quote))
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT bid_size, ask_size, size_unit, lot_size "
                "FROM intraday_quotes"
            ).fetchone()
        self.assertEqual(row, (300.0, 400.0, "shares", 100))

    def test_trade_correction_and_cancel_preserve_raw_and_replay_effective_trades(self):
        original = replace(
            trade(sequence="trade-1"),
            event_ts_ns=1784903400000000000,
        )
        correction = TradeCorrectionEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=AT + timedelta(seconds=1),
            received_ts=AT,
            event_ts_ns=1784903401000000000,
            provider_trade_id="trade-1",
            replacement_trade_id="trade-2",
            price=151.0,
            size=200.0,
            exchange="V",
            conditions=("@",),
            session="regular",
        )
        self.assertEqual(self.store.write_events((original, correction, correction)), 2)
        replayed_correction = replace(
            correction,
            received_ts=AT + timedelta(seconds=10),
        )
        self.assertEqual(
            self.store.write_events((replayed_correction,)),
            0,
        )
        effective = self.store.read_effective_trades("alpaca", "AMD")
        self.assertEqual(len(effective), 1)
        self.assertEqual((effective[0].price, effective[0].size), (151.0, 200.0))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM intraday_trades"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM intraday_trade_corrections"
                ).fetchone()[0],
                1,
            )

        cancel = TradeCancelEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=AT + timedelta(seconds=2),
            received_ts=AT,
            event_ts_ns=1784903402000000000,
            provider_trade_id="trade-1",
            cancel_code="cancel",
            session="regular",
        )
        self.assertEqual(self.store.write_events((cancel, cancel)), 1)
        replayed_cancel = replace(
            cancel,
            received_ts=AT + timedelta(seconds=10),
        )
        self.assertEqual(self.store.write_events((replayed_cancel,)), 0)
        self.assertEqual(self.store.read_effective_trades("alpaca", "AMD"), [])

    def test_effective_replay_follows_correction_chain_and_corrected_cancel(self):
        original = replace(
            trade(sequence="trade-1"),
            event_ts_ns=1784903400000000000,
        )
        first = TradeCorrectionEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=AT + timedelta(seconds=1),
            received_ts=AT + timedelta(seconds=1),
            event_ts_ns=1784903401000000000,
            provider_trade_id="trade-1",
            replacement_trade_id="trade-2",
            price=151.0,
            size=200.0,
            exchange="V",
            conditions=("@",),
            session="regular",
        )
        second = TradeCorrectionEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=AT + timedelta(seconds=2),
            received_ts=AT + timedelta(seconds=2),
            event_ts_ns=1784903402000000000,
            provider_trade_id="trade-2",
            replacement_trade_id="trade-3",
            price=152.0,
            size=300.0,
            exchange="V",
            conditions=("T",),
            session="regular",
        )
        self.store.write_events((original, first, second))

        effective = self.store.read_effective_trades("alpaca", "AMD")
        self.assertEqual(len(effective), 1)
        self.assertEqual(effective[0].source_sequence, "trade-3")
        self.assertEqual(
            (effective[0].price, effective[0].size),
            (152.0, 300.0),
        )

        cancel = TradeCancelEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=AT + timedelta(seconds=3),
            received_ts=AT + timedelta(seconds=3),
            event_ts_ns=1784903403000000000,
            provider_trade_id="trade-3",
            cancel_code="cancel",
            session="regular",
        )
        self.store.write_event(cancel)
        self.assertEqual(
            self.store.read_effective_trades("alpaca", "AMD"),
            [],
        )

    def test_initialize_migrates_preceding_intraday_schema_without_touching_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE prices (ticker TEXT PRIMARY KEY, close REAL);
                    INSERT INTO prices VALUES ('AMD', 150.0);
                    CREATE TABLE intraday_quotes (
                        provider TEXT NOT NULL, symbol TEXT NOT NULL,
                        event_ts TEXT NOT NULL, received_ts TEXT NOT NULL,
                        bid_price REAL NOT NULL, bid_size REAL NOT NULL,
                        ask_price REAL NOT NULL, ask_size REAL NOT NULL,
                        source_sequence TEXT NOT NULL, session TEXT NOT NULL,
                        PRIMARY KEY (provider, symbol, source_sequence)
                    );
                    INSERT INTO intraday_quotes VALUES (
                        'alpaca', 'AMD', '2026-07-24T14:30:00+00:00',
                        '2026-07-24T14:30:01+00:00', 149, 3, 151, 4,
                        'legacy-1', 'regular'
                    );
                    """
                )
            IntradayStore(path).initialize()
            with sqlite3.connect(path) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(intraday_quotes)"
                    )
                }
                price = connection.execute(
                    "SELECT ticker, close FROM prices"
                ).fetchone()
                migrated = connection.execute(
                    "SELECT event_ts_ns, trading_date, size_unit, lot_size, "
                    "bid_size, ask_size "
                    "FROM intraday_quotes"
                ).fetchone()
            self.assertTrue(
                {"event_ts_ns", "trading_date", "size_unit", "lot_size"}
                <= columns
            )
            self.assertEqual(price, ("AMD", 150.0))
            self.assertEqual(
                migrated,
                (
                    1784903400000000000,
                    "2026-07-24",
                    "shares",
                    100,
                    300.0,
                    400.0,
                ),
            )

    def test_effective_trade_adjustments_are_scoped_by_provider_and_symbol(self):
        amd = replace(
            trade(sequence="shared-id"),
            symbol="AMD",
            event_ts_ns=1784903400000000000,
        )
        nvda = replace(amd, symbol="NVDA", price=170.0)
        correction = TradeCorrectionEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=AT + timedelta(seconds=1),
            received_ts=AT + timedelta(seconds=1),
            event_ts_ns=1784903401000000000,
            provider_trade_id="shared-id",
            replacement_trade_id="replacement-id",
            price=151.0,
            size=200.0,
            exchange="V",
            conditions=("@",),
            session="regular",
        )
        self.assertEqual(
            self.store.write_events((amd, nvda, correction)),
            3,
        )
        effective = {
            event.symbol: event
            for event in self.store.read_effective_trades()
        }
        self.assertEqual(effective["AMD"].price, 151.0)
        self.assertEqual(effective["NVDA"].price, 170.0)

    def test_new_session_closes_stale_crashed_session_intervals(self):
        old_heartbeat = AT - timedelta(minutes=1)
        self.store.begin_collector_session(
            session_id="old-session",
            provider="alpaca",
            coverage="iex",
            started_at=old_heartbeat,
            stale_after_seconds=30,
        )
        self.store.open_subscription(
            "alpaca",
            ("AMD",),
            old_heartbeat,
            session_id="old-session",
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO subscription_intervals "
                "(provider, symbol, started_at, finished_at, session_id) "
                "VALUES ('alpaca', 'NVDA', ?, NULL, 'orphaned-session')",
                (old_heartbeat.isoformat(),),
            )
        self.store.write_collector_status(
            session_id="old-session",
            provider="alpaca",
            coverage="iex",
            state="running",
            confirmed_symbols=("AMD",),
            last_event_received_at=None,
            disconnect_count=0,
            error=None,
            heartbeat_at=old_heartbeat,
            queue_depth=0,
            queue_high_water=0,
            dropped_event_count=0,
            undrained_event_count=0,
        )

        self.store.begin_collector_session(
            session_id="new-session",
            provider="alpaca",
            coverage="iex",
            started_at=AT,
            stale_after_seconds=30,
        )

        with sqlite3.connect(self.path) as connection:
            finished = connection.execute(
                "SELECT session_id, finished_at FROM subscription_intervals "
                "ORDER BY session_id"
            ).fetchall()
        self.assertEqual(
            finished,
            [
                ("old-session", AT.isoformat()),
                ("orphaned-session", AT.isoformat()),
            ],
        )
        status = self.store.read_collector_status(
            now=AT,
            stale_after_seconds=30,
        )
        self.assertEqual(status["session_id"], "new-session")
        self.assertEqual(status["state"], "connecting")
        self.assertEqual(status["subscribed_symbols"], [])

    def test_persisted_collector_status_distinguishes_stale_and_never_configured(self):
        never_configured = self.store.read_collector_status(now=AT)
        self.assertEqual(never_configured["state"], "unavailable")
        self.assertEqual(
            never_configured["error"],
            "collector_not_configured",
        )
        self.store.begin_collector_session(
            session_id="session-1",
            provider="alpaca",
            coverage="iex",
            started_at=AT,
            stale_after_seconds=30,
        )
        self.store.write_collector_status(
            session_id="session-1",
            provider="alpaca",
            coverage="iex",
            state="running",
            confirmed_symbols=("SPY", "AMD"),
            last_event_received_at=AT,
            disconnect_count=2,
            error=None,
            heartbeat_at=AT,
            queue_depth=3,
            queue_high_water=7,
            dropped_event_count=0,
            undrained_event_count=0,
        )
        running = self.store.read_collector_status(
            now=AT + timedelta(seconds=5),
            stale_after_seconds=30,
        )
        self.assertEqual(running["state"], "running")
        self.assertEqual(running["subscribed_symbols"], ["SPY", "AMD"])
        self.assertEqual(running["queue_depth"], 3)

        stale = self.store.read_collector_status(
            now=AT + timedelta(seconds=31),
            stale_after_seconds=30,
        )
        self.assertEqual(stale["state"], "stale")
        self.assertEqual(stale["error"], "collector_stale")
        self.assertEqual(stale["session_id"], "session-1")

    def test_takeover_fences_old_status_heartbeat_and_interval_writes(self):
        old_at = AT - timedelta(minutes=1)
        self.store.begin_collector_session(
            session_id="old-session",
            provider="alpaca",
            coverage="iex",
            started_at=old_at,
            stale_after_seconds=30,
        )
        self.store.open_subscription(
            "alpaca",
            ("AMD",),
            old_at,
            session_id="old-session",
        )
        self.store.begin_collector_session(
            session_id="new-session",
            provider="alpaca",
            coverage="iex",
            started_at=AT,
            stale_after_seconds=30,
        )

        stale_status = {
            "session_id": "old-session",
            "provider": "alpaca",
            "coverage": "iex",
            "state": "running",
            "confirmed_symbols": ("AMD",),
            "last_event_received_at": None,
            "disconnect_count": 0,
            "error": None,
            "heartbeat_at": AT + timedelta(seconds=1),
            "queue_depth": 0,
            "queue_high_water": 0,
            "dropped_event_count": 0,
            "undrained_event_count": 0,
        }
        with self.assertRaisesRegex(
            RuntimeError,
            "^collector_session_fenced$",
        ):
            self.store.write_collector_status(**stale_status)
        with self.assertRaisesRegex(
            RuntimeError,
            "^collector_session_fenced$",
        ):
            self.store.open_subscription(
                "alpaca",
                ("NVDA",),
                AT + timedelta(seconds=1),
                session_id="old-session",
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "^collector_session_fenced$",
        ):
            self.store.close_subscription(
                "alpaca",
                ("AMD",),
                AT + timedelta(seconds=1),
                session_id="old-session",
            )

        status = self.store.read_collector_status(now=AT)
        self.assertEqual(status["session_id"], "new-session")
        self.assertEqual(status["state"], "connecting")
        with sqlite3.connect(self.path) as connection:
            open_rows = connection.execute(
                "SELECT session_id, symbol FROM subscription_intervals "
                "WHERE finished_at IS NULL"
            ).fetchall()
        self.assertEqual(open_rows, [])

    def test_takeover_fences_old_session_event_batch(self):
        old_at = AT - timedelta(minutes=1)
        self.store.begin_collector_session(
            session_id="old-session",
            provider="alpaca",
            coverage="iex",
            started_at=old_at,
            stale_after_seconds=30,
        )
        self.store.begin_collector_session(
            session_id="new-session",
            provider="alpaca",
            coverage="iex",
            started_at=AT,
            stale_after_seconds=30,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "^collector_session_fenced$",
        ):
            self.store.write_events(
                (trade(sequence="old-owner-trade"),),
                session_id="old-session",
            )

        with sqlite3.connect(self.path) as connection:
            persisted = connection.execute(
                "SELECT COUNT(*) FROM intraday_trades"
            ).fetchone()[0]
        self.assertEqual(persisted, 0)

    def test_status_heartbeat_cannot_move_backward(self):
        self.store.begin_collector_session(
            session_id="session-1",
            provider="alpaca",
            coverage="iex",
            started_at=AT,
            stale_after_seconds=30,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "^collector_status_time_regression$",
        ):
            self.store.begin_collector_session(
                session_id="session-1",
                provider="alpaca",
                coverage="iex",
                started_at=AT - timedelta(seconds=1),
                stale_after_seconds=30,
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "^collector_status_time_regression$",
        ):
            self.store.write_collector_status(
                session_id="session-1",
                provider="alpaca",
                coverage="iex",
                state="running",
                confirmed_symbols=("AMD",),
                last_event_received_at=None,
                disconnect_count=0,
                error=None,
                heartbeat_at=AT - timedelta(seconds=1),
                queue_depth=0,
                queue_high_water=0,
                dropped_event_count=0,
                undrained_event_count=0,
            )
        self.assertEqual(
            self.store.read_collector_status(now=AT)["heartbeat_at"],
            AT.isoformat(),
        )

    def test_subscription_reconciliation_and_status_are_one_transaction(self):
        self.store.begin_collector_session(
            session_id="session-1",
            provider="alpaca",
            coverage="iex",
            started_at=AT,
            stale_after_seconds=30,
        )
        first_at = AT + timedelta(seconds=1)
        self.store.reconcile_collector_subscription(
            session_id="session-1",
            provider="alpaca",
            coverage="iex",
            state="running",
            confirmed_symbols=("AMD",),
            reconciled_at=first_at,
            last_event_received_at=None,
            disconnect_count=0,
            error=None,
            queue_depth=0,
            queue_high_water=0,
            dropped_event_count=0,
            undrained_event_count=0,
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_collector_status_update
                BEFORE UPDATE ON collector_status
                BEGIN
                    SELECT RAISE(ABORT, 'forced status failure');
                END
                """
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reconcile_collector_subscription(
                session_id="session-1",
                provider="alpaca",
                coverage="iex",
                state="running",
                confirmed_symbols=("AMD", "NVDA"),
                reconciled_at=AT + timedelta(seconds=2),
                last_event_received_at=None,
                disconnect_count=0,
                error=None,
                queue_depth=0,
                queue_high_water=0,
                dropped_event_count=0,
                undrained_event_count=0,
            )

        with sqlite3.connect(self.path) as connection:
            open_symbols = connection.execute(
                "SELECT symbol FROM subscription_intervals "
                "WHERE finished_at IS NULL ORDER BY symbol"
            ).fetchall()
        self.assertEqual(open_symbols, [("AMD",)])
        status = self.store.read_collector_status(
            now=first_at,
            stale_after_seconds=30,
        )
        self.assertEqual(status["subscribed_symbols"], ["AMD"])
        self.assertEqual(status["heartbeat_at"], first_at.isoformat())


if __name__ == "__main__":
    unittest.main()
