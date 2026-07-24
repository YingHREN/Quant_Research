import asyncio
from datetime import datetime, timezone
import threading
import unittest

from marketdata.base import (
    ProviderCapabilities,
    SubscriptionConfirmation,
    TradeEvent,
)
from marketdata.collector import IntradayCollector


class FakeStore:
    def __init__(self):
        self.events = []
        self.opened = []
        self.closed = []
        self.lifecycle = []
        self.capabilities = []
        self.batches = []
        self.statuses = []
        self.sessions = []
        self.reconciliations = []
        self.active_symbols = ()

    def initialize(self):
        pass

    def record_capabilities(self, capability, at):
        self.capabilities.append((capability, at))

    def write_event(self, event):
        self.events.append(event)
        return True

    def write_events(self, events):
        batch = tuple(events)
        self.batches.append(batch)
        self.events.extend(batch)
        return len(batch)

    def begin_collector_session(self, *args, **kwargs):
        self.sessions.append((args, kwargs))

    def write_collector_status(self, *args, **kwargs):
        self.statuses.append((args, kwargs))

    def reconcile_collector_subscription(self, *args, **kwargs):
        confirmed = tuple(kwargs["confirmed_symbols"])
        at = kwargs["reconciled_at"]
        provider = kwargs["provider"]
        previous = self.active_symbols
        opening = tuple(
            symbol for symbol in confirmed if symbol not in previous
        )
        closing = tuple(
            symbol for symbol in previous if symbol not in confirmed
        )
        if opening:
            self.open_subscription(
                provider,
                opening,
                at,
                session_id=kwargs["session_id"],
            )
        if closing:
            self.close_subscription(
                provider,
                closing,
                at,
                session_id=kwargs["session_id"],
            )
        self.active_symbols = confirmed
        self.reconciliations.append(confirmed)
        status = dict(kwargs)
        status["heartbeat_at"] = status.pop("reconciled_at")
        self.write_collector_status(**status)

    def open_subscription(
        self,
        provider,
        symbols,
        at,
        session_id=None,
    ):
        self.opened.append((provider, symbols))
        self.lifecycle.append(("open", provider, symbols, at))

    def close_subscription(
        self,
        provider,
        symbols,
        at,
        session_id=None,
    ):
        self.closed.append((provider, symbols))
        self.lifecycle.append(("close", provider, symbols, at))


class FailOnceCloseStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.close_attempts = 0

    def close_subscription(
        self,
        provider,
        symbols,
        at,
        session_id=None,
    ):
        self.close_attempts += 1
        if self.close_attempts == 1:
            raise RuntimeError("sensitive close detail")
        super().close_subscription(
            provider,
            symbols,
            at,
            session_id=session_id,
        )


class FakeProvider:
    def __init__(self):
        self.requests = []
        self.updated = []
        self.close_calls = 0

    def capabilities(self):
        from marketdata.base import ProviderCapabilities

        return ProviderCapabilities(
            "fake",
            "iex",
            True,
            30,
            False,
            False,
            True,
            False,
            False,
            None,
        )

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        await asyncio.Event().wait()

    async def update_subscription(self, request, on_confirmed=None):
        self.updated.append(request.symbols)
        return await confirm_subscription(
            on_confirmed,
            request.symbols,
        )

    async def close(self):
        self.close_calls += 1


async def eventually(predicate, timeout=0.5):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout)


async def confirm_subscription(on_confirmed, symbols):
    confirmation = SubscriptionConfirmation(symbols, symbols)
    if on_confirmed is not None:
        result = on_confirmed(confirmation)
        if asyncio.iscoroutine(result):
            await result
    return confirmation


class FailingProvider(FakeProvider):
    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        raise RuntimeError("sensitive provider detail")


class BlockingUpdateProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.first_update_started = asyncio.Event()
        self.release_first_update = asyncio.Event()
        self.concurrent_updates = 0
        self.max_concurrent_updates = 0
        self.update_cancelled = False

    async def update_subscription(self, request, on_confirmed=None):
        self.updated.append(request.symbols)
        self.concurrent_updates += 1
        self.max_concurrent_updates = max(
            self.max_concurrent_updates,
            self.concurrent_updates,
        )
        try:
            if len(self.updated) == 1:
                self.first_update_started.set()
                await self.release_first_update.wait()
        except asyncio.CancelledError:
            self.update_cancelled = True
            raise
        finally:
            self.concurrent_updates -= 1
        return await confirm_subscription(
            on_confirmed,
            request.symbols,
        )


class SlowCancelUpdateProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.update_started = asyncio.Event()
        self.update_cancellation_started = asyncio.Event()
        self.finish_update_cancellation = asyncio.Event()
        self.update_finished = asyncio.Event()

    async def update_subscription(self, request, on_confirmed=None):
        self.updated.append(request.symbols)
        self.update_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.update_cancellation_started.set()
            while not self.finish_update_cancellation.is_set():
                try:
                    await self.finish_update_cancellation.wait()
                except asyncio.CancelledError:
                    continue
            raise
        finally:
            self.update_finished.set()


class FailingUpdateProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.update_attempted = asyncio.Event()

    async def update_subscription(self, request, on_confirmed=None):
        self.updated.append(request.symbols)
        self.update_attempted.set()
        raise RuntimeError("sensitive update detail")


class UnavailableProvider(FakeProvider):
    def capabilities(self):
        return ProviderCapabilities(
            "fake",
            "iex",
            True,
            30,
            False,
            False,
            True,
            False,
            False,
            "missing_credentials",
        )

    async def stream_events(self, request, emit, on_confirmed=None):
        raise AssertionError("unavailable provider must not connect")


class RetryOnceProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.second_attempt_started = asyncio.Event()

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        if len(self.requests) == 1:
            raise RuntimeError("sensitive stream detail")
        self.second_attempt_started.set()
        await asyncio.Event().wait()


class EmittingProvider(FakeProvider):
    def __init__(self, event):
        super().__init__()
        self.event = event
        self.emitted = asyncio.Event()

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        await emit(self.event)
        self.emitted.set()
        await asyncio.Event().wait()


class DisconnectDuringUpdateProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.first_stream_started = asyncio.Event()
        self.fail_first_stream = asyncio.Event()
        self.second_stream_started = asyncio.Event()
        self.update_started = asyncio.Event()
        self.release_update = asyncio.Event()
        self.update_cancelled = False

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        if len(self.requests) == 1:
            self.first_stream_started.set()
            await self.fail_first_stream.wait()
            raise RuntimeError("first stream failed")
        self.second_stream_started.set()
        await asyncio.Event().wait()

    async def update_subscription(self, request, on_confirmed=None):
        self.updated.append(request.symbols)
        self.update_started.set()
        try:
            await self.release_update.wait()
        except asyncio.CancelledError:
            self.update_cancelled = True
            raise
        return await confirm_subscription(
            on_confirmed,
            request.symbols,
        )


class FailUpdateThenReconnectProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.first_stream_started = asyncio.Event()
        self.fail_first_stream = asyncio.Event()
        self.second_stream_started = asyncio.Event()

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        if len(self.requests) == 1:
            self.first_stream_started.set()
            await self.fail_first_stream.wait()
            raise RuntimeError("first stream failed")
        self.second_stream_started.set()
        await asyncio.Event().wait()

    async def update_subscription(self, request, on_confirmed=None):
        self.updated.append(request.symbols)
        raise RuntimeError("update failed")


class RestartableProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.stream_releases = []

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        release = asyncio.Event()
        self.stream_releases.append(release)
        await release.wait()

    async def close(self):
        await super().close()
        if self.stream_releases:
            self.stream_releases[-1].set()


class SlowCloseProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self):
        self.close_calls += 1
        self.close_started.set()
        await self.release_close.wait()


class CloseCancelsStreamProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.stream_started = asyncio.Event()
        self.stream_task = None

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        self.stream_task = asyncio.current_task()
        self.stream_started.set()
        await asyncio.Event().wait()

    async def close(self):
        self.close_calls += 1
        stream_task = self.stream_task
        if (
            stream_task is not None
            and stream_task is not asyncio.current_task()
            and not stream_task.done()
        ):
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass


class CancelThenSlowCloseProvider(CloseCancelsStreamProvider):
    def __init__(self):
        super().__init__()
        self.stream_joined = asyncio.Event()
        self.release_first_close = asyncio.Event()

    async def close(self):
        self.close_calls += 1
        stream_task = self.stream_task
        if (
            stream_task is not None
            and stream_task is not asyncio.current_task()
            and not stream_task.done()
        ):
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
        self.stream_joined.set()
        if self.close_calls == 1:
            await self.release_first_close.wait()


class FailOnceUpdateProvider(FakeProvider):
    async def update_subscription(self, request, on_confirmed=None):
        self.updated.append(request.symbols)
        if len(self.updated) == 1:
            raise RuntimeError("first update failed")
        return await confirm_subscription(
            on_confirmed,
            request.symbols,
        )


def market_event(sequence):
    at = datetime(2026, 7, 24, 14, 30, sequence, tzinfo=timezone.utc)
    return TradeEvent(
        "fake",
        "AMD",
        at,
        at,
        150.0 + sequence,
        10.0,
        "V",
        (),
        "unknown",
        "unknown",
        str(sequence),
        "regular",
    )


class DelayedInitialConfirmationProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.stream_started = asyncio.Event()
        self.release_confirmation = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        self.stream_started.set()
        await self.release_confirmation.wait()
        await confirm_subscription(on_confirmed, request.symbols)
        await self.closed.wait()

    async def close(self):
        await super().close()
        self.closed.set()


class BurstProvider(FakeProvider):
    def __init__(self, events):
        super().__init__()
        self.events = tuple(events)
        self.accepted = 0
        self.emitted = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream_events(self, request, emit, on_confirmed=None):
        self.requests.append(request.symbols)
        await confirm_subscription(on_confirmed, request.symbols)
        for event in self.events:
            await emit(event)
            self.accepted += 1
        self.emitted.set()
        await self.closed.wait()

    async def close(self):
        await super().close()
        self.closed.set()


class BlockingBatchStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.write_started = threading.Event()
        self.release_write = threading.Event()

    def write_events(self, events):
        batch = tuple(events)
        self.write_started.set()
        if not self.release_write.wait(timeout=2):
            raise RuntimeError("test writer release timed out")
        self.batches.append(batch)
        self.events.extend(batch)
        return len(batch)


class FailingBatchStore(FakeStore):
    def write_event(self, event):
        raise RuntimeError("sensitive sqlite detail")

    def write_events(self, events):
        raise RuntimeError("sensitive sqlite detail")


class FailingInitializeStore(FakeStore):
    def initialize(self):
        raise RuntimeError("sensitive startup sqlite detail")


class BlockingStatusStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.first_write_started = threading.Event()
        self.second_write_started = threading.Event()
        self.release_first_write = threading.Event()
        self._write_count = 0
        self._write_count_lock = threading.Lock()

    def write_collector_status(self, *args, **kwargs):
        with self._write_count_lock:
            self._write_count += 1
            write_count = self._write_count
        if write_count == 1:
            self.first_write_started.set()
            if not self.release_first_write.wait(timeout=2):
                raise RuntimeError("test status release timed out")
        elif write_count == 2:
            self.second_write_started.set()
        super().write_collector_status(*args, **kwargs)


class PartialAckThenFailUpdateProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.partial_ack_reported = asyncio.Event()

    async def update_subscription(
        self,
        request,
        on_confirmed=None,
    ):
        self.updated.append(request.symbols)
        confirmation = SubscriptionConfirmation(
            ("SPY", "QQQ", "SOXX", "AMD", "NVDA"),
            ("SPY", "QQQ", "SOXX", "AMD", "NVDA"),
        )
        if on_confirmed is not None:
            result = on_confirmed(confirmation)
            if asyncio.iscoroutine(result):
                await result
        self.partial_ack_reported.set()
        raise RuntimeError("sensitive remove failure")


class IntradayCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_status_writes_are_serialized(self):
        store = BlockingStatusStore()
        collector = IntradayCollector(FakeProvider(), store)
        collector._session_id = "session"
        first = asyncio.create_task(asyncio.to_thread(collector._publish_status))
        await asyncio.to_thread(store.first_write_started.wait, 0.5)
        second = asyncio.create_task(
            asyncio.to_thread(collector._publish_status)
        )

        second_entered_while_first_blocked = await asyncio.to_thread(
            store.second_write_started.wait,
            0.05,
        )
        store.release_first_write.set()
        await asyncio.gather(first, second)

        self.assertFalse(second_entered_while_first_blocked)

    async def test_each_ack_reconciles_actual_symbols_before_later_update_failure(self):
        provider = PartialAckThenFailUpdateProvider()
        store = FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")
        store.lifecycle.clear()

        collector.set_selection("NVDA", [], [])
        await provider.partial_ack_reported.wait()
        await eventually(
            lambda: collector.snapshot()["error"] == "provider_error"
        )

        try:
            self.assertEqual(
                collector.snapshot()["subscribed_symbols"],
                ["SPY", "QQQ", "SOXX", "AMD", "NVDA"],
            )
            self.assertEqual(
                store.reconciliations[-1],
                ("SPY", "QQQ", "SOXX", "AMD", "NVDA"),
            )
            self.assertEqual(
                [
                    (operation, symbols)
                    for operation, _provider, symbols, _at
                    in store.lifecycle
                ],
                [("open", ("NVDA",))],
            )
        finally:
            await collector.stop()
            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run_task

    async def test_cleanup_interval_storage_error_still_closes_provider(self):
        provider = FakeProvider()
        store = FailOnceCloseStore()
        collector = IntradayCollector(provider, store)
        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")

        with self.assertRaisesRegex(
            RuntimeError,
            "^collector_storage_failed$",
        ):
            await collector.stop()

        self.assertEqual(provider.close_calls, 1)
        self.assertEqual(collector.snapshot()["state"], "collector_error")
        self.assertEqual(collector.snapshot()["error"], "storage_error")
        self.assertEqual(collector.snapshot()["disconnect_count"], 0)
        if not run_task.done():
            run_task.cancel()
        with self.assertRaises(
            (asyncio.CancelledError, RuntimeError),
        ):
            await run_task

    async def test_collector_waits_for_provider_confirmation_before_running_or_opening(self):
        provider = DelayedInitialConfirmationProvider()
        store = FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await provider.stream_started.wait()

        self.assertEqual(collector.snapshot()["state"], "connecting")
        self.assertEqual(collector.snapshot()["subscribed_symbols"], [])
        self.assertEqual(store.opened, [])

        provider.release_confirmation.set()
        await eventually(lambda: collector.snapshot()["state"] == "running")
        self.assertIsNotNone(collector.snapshot()["heartbeat_at"])
        self.assertEqual(
            collector.snapshot()["subscribed_symbols"],
            ["SPY", "QQQ", "SOXX", "AMD"],
        )
        self.assertEqual(
            store.opened,
            [("fake", ("SPY", "QQQ", "SOXX", "AMD"))],
        )
        await collector.stop()
        await run_task

    async def test_startup_storage_error_is_typed_without_connect_or_disconnect(self):
        provider = FakeProvider()
        collector = IntradayCollector(provider, FailingInitializeStore())

        await collector.run()

        snapshot = collector.snapshot()
        self.assertEqual(snapshot["state"], "collector_error")
        self.assertEqual(snapshot["error"], "storage_error")
        self.assertEqual(snapshot["disconnect_count"], 0)
        self.assertEqual(provider.requests, [])
        self.assertNotIn("sensitive startup sqlite detail", str(snapshot))

    async def test_burst_events_use_bounded_queue_and_batch_storage_api(self):
        provider = BurstProvider(market_event(index) for index in range(5))
        store = FakeStore()
        collector = IntradayCollector(
            provider,
            store,
            queue_size=8,
            batch_size=3,
        )
        run_task = asyncio.create_task(collector.run())
        await provider.emitted.wait()
        await eventually(lambda: len(store.events) == 5)

        snapshot = collector.snapshot()
        self.assertEqual(sum(len(batch) for batch in store.batches), 5)
        self.assertGreater(max(len(batch) for batch in store.batches), 1)
        self.assertLessEqual(snapshot["queue_high_water"], 8)
        self.assertEqual(snapshot["dropped_event_count"], 0)
        await collector.stop()
        await run_task

    async def test_queue_full_applies_lossless_backpressure(self):
        provider = BurstProvider(market_event(index) for index in range(3))
        store = BlockingBatchStore()
        collector = IntradayCollector(
            provider,
            store,
            queue_size=1,
            batch_size=1,
        )
        run_task = asyncio.create_task(collector.run())
        await asyncio.to_thread(store.write_started.wait, 0.5)
        await eventually(lambda: provider.accepted >= 2)
        self.assertEqual(provider.accepted, 2)
        self.assertFalse(provider.emitted.is_set())
        self.assertEqual(collector.snapshot()["dropped_event_count"], 0)

        store.release_write.set()
        await provider.emitted.wait()
        await eventually(lambda: len(store.events) == 3)
        await collector.stop()
        await run_task

    async def test_storage_error_is_typed_and_not_counted_as_provider_disconnect(self):
        provider = BurstProvider((market_event(1),))
        store = FailingBatchStore()
        collector = IntradayCollector(
            provider,
            store,
            retry_delays=(60,),
            queue_size=2,
            batch_size=2,
        )
        run_task = asyncio.create_task(collector.run())
        try:
            await eventually(
                lambda: collector.snapshot()["state"] == "collector_error"
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "^collector_storage_failed$",
            ):
                await asyncio.wait_for(run_task, 0.5)
            snapshot = collector.snapshot()
            self.assertEqual(snapshot["error"], "storage_error")
            self.assertEqual(snapshot["disconnect_count"], 0)
            self.assertGreaterEqual(snapshot["undrained_event_count"], 1)
            self.assertNotIn("sensitive sqlite detail", str(snapshot))
        finally:
            if not run_task.done():
                await collector.stop()
                run_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await run_task

    async def test_shutdown_waits_for_queue_drain(self):
        provider = BurstProvider(market_event(index) for index in range(3))
        store = BlockingBatchStore()
        collector = IntradayCollector(
            provider,
            store,
            queue_size=8,
            batch_size=3,
        )
        run_task = asyncio.create_task(collector.run())
        await provider.emitted.wait()
        await asyncio.to_thread(store.write_started.wait, 0.5)
        stop_task = asyncio.create_task(collector.stop())
        await asyncio.sleep(0)
        try:
            self.assertFalse(stop_task.done())
        finally:
            store.release_write.set()
        await stop_task
        await run_task
        self.assertEqual(store.events, list(provider.events))
        self.assertEqual(
            collector.snapshot()["undrained_event_count"],
            0,
        )

    async def test_selection_builds_expected_pool_and_updates_connected_provider(self):
        provider, store = FakeProvider(), FakeStore()
        collector = IntradayCollector(provider, store, retry_delays=(0,))
        collector.set_selection("AMD", ["NVDA", "AVGO"], ["NBIS"])
        task = asyncio.create_task(collector.run())
        await eventually(lambda: bool(provider.requests))
        self.assertEqual(provider.requests[0][:4], ("SPY", "QQQ", "SOXX", "AMD"))

        collector.set_selection("NBIS", ["AMD"], [])
        await eventually(lambda: bool(provider.updated))
        self.assertEqual(provider.updated[-1][:4], ("SPY", "QQQ", "SOXX", "NBIS"))
        await collector.stop()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_snapshot_never_contains_credentials(self):
        collector = IntradayCollector(FakeProvider(), FakeStore())
        value = collector.snapshot()
        self.assertEqual(value["coverage"], "iex")
        self.assertNotIn("secret", str(value).lower())

    async def test_stop_interrupts_retry_wait_without_closing_interval_twice(self):
        provider, store = FailingProvider(), FakeStore()
        collector = IntradayCollector(provider, store, retry_delays=(60,))
        task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "retrying")

        await asyncio.wait_for(collector.stop(), 0.2)
        await asyncio.wait_for(task, 0.2)

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(store.closed, [("fake", ("SPY", "QQQ", "SOXX"))])

    async def test_concurrent_selection_updates_are_serialized_and_last_value_wins(self):
        provider, store = BlockingUpdateProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")

        collector.set_selection("NVDA", [], [])
        await provider.first_update_started.wait()
        collector.set_selection("AVGO", [], [])
        collector.set_selection("NBIS", [], [])
        await asyncio.sleep(0)

        try:
            self.assertEqual(len(provider.updated), 1)
            provider.release_first_update.set()
            await eventually(
                lambda: collector.snapshot()["subscribed_symbols"][:4]
                == ["SPY", "QQQ", "SOXX", "NBIS"]
            )
            self.assertEqual(provider.max_concurrent_updates, 1)
            self.assertEqual(
                [symbols[3] for symbols in provider.updated],
                ["NVDA", "NBIS"],
            )
        finally:
            provider.release_first_update.set()
            await collector.stop()
            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run_task

    async def test_subscription_update_error_is_redacted_in_snapshot(self):
        provider, store = FailingUpdateProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")

        collector.set_selection("NVDA", [], [])
        await provider.update_attempted.wait()
        await asyncio.sleep(0)

        try:
            snapshot = collector.snapshot()
            self.assertEqual(snapshot["state"], "running")
            self.assertEqual(snapshot["error"], "provider_error")
            self.assertNotIn("sensitive update detail", str(snapshot))
            self.assertEqual(snapshot["subscribed_symbols"][3], "AMD")
            self.assertEqual(snapshot["desired_symbols"][3], "NVDA")
        finally:
            await collector.stop()
            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run_task

    async def test_unavailable_provider_records_capability_without_connecting(self):
        provider, store = UnavailableProvider(), FakeStore()
        collector = IntradayCollector(provider, store)

        await collector.run()

        snapshot = collector.snapshot()
        self.assertEqual(snapshot["state"], "unavailable")
        self.assertEqual(snapshot["error"], "missing_credentials")
        self.assertEqual(provider.requests, [])
        self.assertEqual(store.opened, [])
        self.assertEqual(len(store.capabilities), 1)

    async def test_retry_closes_before_reopening_subscription_interval(self):
        provider, store = RetryOnceProvider(), FakeStore()
        collector = IntradayCollector(provider, store, retry_delays=(0,))
        task = asyncio.create_task(collector.run())
        await provider.second_attempt_started.wait()

        try:
            self.assertEqual(
                [operation for operation, *_rest in store.lifecycle[:3]],
                ["open", "close", "open"],
            )
            self.assertEqual(store.lifecycle[0][2], store.lifecycle[1][2])
            self.assertEqual(store.lifecycle[1][2], store.lifecycle[2][2])
            snapshot = collector.snapshot()
            self.assertEqual(snapshot["state"], "running")
            self.assertEqual(snapshot["disconnect_count"], 1)
            self.assertIsNone(snapshot["error"])
        finally:
            await collector.stop()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_pool_change_opens_new_interval_before_closing_old_one(self):
        provider, store = FakeProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")
        store.lifecycle.clear()

        collector.set_selection("NVDA", [], [])
        await eventually(
            lambda: collector.snapshot()["subscribed_symbols"][3] == "NVDA"
        )

        try:
            self.assertEqual(
                [
                    (operation, symbols)
                    for operation, _provider, symbols, _at in store.lifecycle
                ],
                [("open", ("NVDA",)), ("close", ("AMD",))],
            )
            self.assertEqual(store.lifecycle[0][3], store.lifecycle[1][3])
        finally:
            await collector.stop()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_event_is_persisted_and_received_time_is_exposed(self):
        received_at = datetime(2026, 7, 24, 14, 30, tzinfo=timezone.utc)
        event = TradeEvent(
            "fake",
            "AMD",
            received_at,
            received_at,
            150.0,
            10.0,
            "V",
            (),
            "unknown",
            "unknown",
            "1",
            "regular",
        )
        provider, store = EmittingProvider(event), FakeStore()
        collector = IntradayCollector(provider, store)
        task = asyncio.create_task(collector.run())
        await provider.emitted.wait()
        await eventually(lambda: store.events == [event])
        await eventually(
            lambda: collector.snapshot()["last_event_received_at"]
            == received_at.isoformat()
        )

        try:
            self.assertEqual(store.events, [event])
            self.assertEqual(
                collector.snapshot()["last_event_received_at"],
                received_at.isoformat(),
            )
        finally:
            await collector.stop()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_retry_cancels_old_update_worker_before_reconnect(self):
        provider, store = DisconnectDuringUpdateProvider(), FakeStore()
        collector = IntradayCollector(provider, store, retry_delays=(0,))
        collector.set_selection("AMD", [], [])
        task = asyncio.create_task(collector.run())
        await provider.first_stream_started.wait()

        collector.set_selection("NVDA", [], [])
        await provider.update_started.wait()
        provider.fail_first_stream.set()
        await provider.second_stream_started.wait()
        provider.release_update.set()
        await asyncio.sleep(0)

        try:
            self.assertTrue(provider.update_cancelled)
            self.assertEqual(
                [
                    (operation, symbols)
                    for operation, _provider, symbols, _at in store.lifecycle
                ],
                [
                    ("open", ("SPY", "QQQ", "SOXX", "AMD")),
                    ("close", ("SPY", "QQQ", "SOXX", "AMD")),
                    ("open", ("SPY", "QQQ", "SOXX", "NVDA")),
                ],
            )
            self.assertEqual(
                collector.snapshot()["subscribed_symbols"],
                ["SPY", "QQQ", "SOXX", "NVDA"],
            )
        finally:
            provider.release_update.set()
            await collector.stop()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_direct_run_cancellation_cleans_up_once_and_propagates(self):
        provider, store = BlockingUpdateProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")
        collector.set_selection("NVDA", [], [])
        await provider.first_update_started.wait()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        try:
            self.assertTrue(provider.update_cancelled)
            self.assertEqual(provider.concurrent_updates, 0)
            self.assertEqual(provider.close_calls, 1)
            self.assertEqual(
                store.closed,
                [("fake", ("SPY", "QQQ", "SOXX", "AMD"))],
            )
            snapshot = collector.snapshot()
            self.assertEqual(snapshot["state"], "stopped")
            self.assertEqual(snapshot["subscribed_symbols"], [])
        finally:
            provider.release_first_update.set()
            if collector.snapshot()["state"] != "stopped":
                await collector.stop()

    async def test_concurrent_run_is_rejected_without_affecting_owner(self):
        provider, store = FakeProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        owner = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")

        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "^collector_already_running$",
            ):
                await asyncio.wait_for(collector.run(), 0.1)
            self.assertFalse(owner.done())
            self.assertEqual(collector.snapshot()["state"], "running")
            self.assertEqual(len(provider.requests), 1)
        finally:
            await collector.stop()
            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner

    async def test_collector_can_run_again_after_stop(self):
        provider, store = RestartableProvider(), FakeStore()
        collector = IntradayCollector(provider, store)

        first = asyncio.create_task(collector.run())
        await eventually(lambda: len(provider.requests) == 1)
        await collector.stop()
        await asyncio.wait_for(first, 0.2)

        second = asyncio.create_task(collector.run())
        try:
            await eventually(lambda: len(provider.requests) == 2)
            self.assertEqual(collector.snapshot()["state"], "running")
            self.assertFalse(second.done())
        finally:
            await collector.stop()
            await asyncio.wait_for(second, 0.2)

        self.assertEqual(provider.close_calls, 2)
        self.assertEqual(len(store.opened), 2)
        self.assertEqual(len(store.closed), 2)

    async def test_successful_update_clears_prior_update_error(self):
        provider, store = FailOnceUpdateProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")

        collector.set_selection("NVDA", [], [])
        await eventually(
            lambda: collector.snapshot()["error"] == "provider_error"
        )
        collector.set_selection("NBIS", [], [])
        await eventually(
            lambda: collector.snapshot()["subscribed_symbols"][3] == "NBIS"
        )

        try:
            self.assertIsNone(collector.snapshot()["error"])
            self.assertEqual(
                [symbols[3] for symbols in provider.updated],
                ["NVDA", "NBIS"],
            )
        finally:
            await collector.stop()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_cancelled_stop_waits_for_cleanup_before_propagating(self):
        provider, store = SlowCancelUpdateProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")
        collector.set_selection("NVDA", [], [])
        await provider.update_started.wait()

        stop_task = asyncio.create_task(collector.stop())
        await provider.update_cancellation_started.wait()
        stop_task.cancel()
        await asyncio.sleep(0)
        try:
            self.assertFalse(stop_task.done())
            self.assertNotEqual(collector.snapshot()["state"], "stopped")
        finally:
            provider.finish_update_cancellation.set()

        with self.assertRaises(asyncio.CancelledError):
            await stop_task

        try:
            self.assertTrue(provider.update_finished.is_set())
            self.assertEqual(provider.close_calls, 1)
            self.assertEqual(
                store.closed,
                [("fake", ("SPY", "QQQ", "SOXX", "AMD"))],
            )
            self.assertEqual(collector.snapshot()["state"], "stopped")
            self.assertEqual(collector.snapshot()["subscribed_symbols"], [])
        finally:
            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run_task

    async def test_retry_to_latest_desired_clears_prior_update_error(self):
        provider, store = FailUpdateThenReconnectProvider(), FakeStore()
        collector = IntradayCollector(provider, store, retry_delays=(0,))
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await provider.first_stream_started.wait()

        collector.set_selection("NVDA", [], [])
        await eventually(
            lambda: collector.snapshot()["error"] == "provider_error"
        )
        provider.fail_first_stream.set()
        await provider.second_stream_started.wait()

        try:
            snapshot = collector.snapshot()
            self.assertEqual(
                snapshot["subscribed_symbols"],
                ["SPY", "QQQ", "SOXX", "NVDA"],
            )
            self.assertIsNone(snapshot["error"])
            self.assertEqual(provider.requests[-1][3], "NVDA")
        finally:
            await collector.stop()
            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run_task

    async def test_reverting_desired_to_active_clears_update_error_without_update(self):
        provider, store = FailingUpdateProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")

        collector.set_selection("NVDA", [], [])
        await eventually(
            lambda: collector.snapshot()["error"] == "provider_error"
        )
        collector.set_selection("AMD", [], [])
        await asyncio.sleep(0)

        try:
            snapshot = collector.snapshot()
            self.assertEqual(
                snapshot["desired_symbols"],
                snapshot["subscribed_symbols"],
            )
            self.assertIsNone(snapshot["error"])
            self.assertEqual(
                [symbols[3] for symbols in provider.updated],
                ["NVDA"],
            )
        finally:
            await collector.stop()
            run_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await run_task

    async def test_late_cancelled_stop_waits_for_inline_run_cleanup(self):
        provider, store = SlowCloseProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: collector.snapshot()["state"] == "running")

        run_task.cancel()
        await provider.close_started.wait()
        stop_task = asyncio.create_task(collector.stop())
        await asyncio.sleep(0)
        stop_task.cancel()
        await asyncio.sleep(0)
        stop_done_before_release = stop_task.done()
        state_before_release = collector.snapshot()["state"]
        provider.release_close.set()

        run_cancelled = False
        try:
            await run_task
        except asyncio.CancelledError:
            run_cancelled = True
        stop_cancelled = False
        try:
            await stop_task
        except asyncio.CancelledError:
            stop_cancelled = True

        self.assertFalse(stop_done_before_release)
        self.assertNotEqual(state_before_release, "stopped")
        self.assertTrue(run_cancelled)
        self.assertTrue(stop_cancelled)
        self.assertEqual(provider.close_calls, 1)
        self.assertEqual(
            store.closed,
            [("fake", ("SPY", "QQQ", "SOXX", "AMD"))],
        )
        self.assertEqual(collector.snapshot()["state"], "stopped")
        self.assertEqual(collector.snapshot()["subscribed_symbols"], [])

    async def test_stop_owned_cleanup_does_not_deadlock_run_finally(self):
        provider, store = CloseCancelsStreamProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await provider.stream_started.wait()

        await asyncio.wait_for(collector.stop(), 0.2)

        with self.assertRaises(asyncio.CancelledError):
            await run_task
        self.assertEqual(provider.close_calls, 1)
        self.assertEqual(
            store.closed,
            [("fake", ("SPY", "QQQ", "SOXX", "AMD"))],
        )
        self.assertEqual(collector.snapshot()["state"], "stopped")
        self.assertEqual(collector.snapshot()["subscribed_symbols"], [])

    async def test_run_is_rejected_while_shared_cleanup_is_pending(self):
        provider, store = CancelThenSlowCloseProvider(), FakeStore()
        collector = IntradayCollector(provider, store)
        run_task = asyncio.create_task(collector.run())
        await provider.stream_started.wait()
        stop_task = asyncio.create_task(collector.stop())
        await provider.stream_joined.wait()
        with self.assertRaises(asyncio.CancelledError):
            await run_task

        admission_error = None
        try:
            await asyncio.wait_for(collector.run(), 0.05)
        except BaseException as caught:
            admission_error = caught
        state_before_release = collector.snapshot()["state"]
        requests_before_release = len(provider.requests)
        provider.release_first_close.set()
        await stop_task

        self.assertIsInstance(admission_error, RuntimeError)
        self.assertEqual(str(admission_error), "collector_stopping")
        self.assertEqual(requests_before_release, 1)
        self.assertNotEqual(state_before_release, "stopped")
        self.assertEqual(collector.snapshot()["state"], "stopped")

        run_task = asyncio.create_task(collector.run())
        await eventually(lambda: len(provider.requests) == 2)
        self.assertEqual(collector.snapshot()["state"], "running")
        await asyncio.wait_for(collector.stop(), 0.2)
        with self.assertRaises(asyncio.CancelledError):
            await run_task
        self.assertEqual(len(provider.requests), 2)

    async def test_failed_cleanup_blocks_run_and_next_stop_retries(self):
        provider, store = CloseCancelsStreamProvider(), FailOnceCloseStore()
        collector = IntradayCollector(provider, store)
        collector.set_selection("AMD", [], [])
        run_task = asyncio.create_task(collector.run())
        await provider.stream_started.wait()

        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "^collector_storage_failed$",
            ):
                await collector.stop()

            failed_snapshot = collector.snapshot()
            self.assertEqual(failed_snapshot["state"], "collector_error")
            self.assertEqual(failed_snapshot["error"], "storage_error")
            self.assertNotIn("sensitive close detail", str(failed_snapshot))
            self.assertEqual(
                failed_snapshot["subscribed_symbols"],
                ["SPY", "QQQ", "SOXX", "AMD"],
            )
            self.assertEqual(store.close_attempts, 1)
            self.assertEqual(provider.close_calls, 1)

            with self.assertRaisesRegex(
                RuntimeError,
                "^collector_cleanup_failed$",
            ):
                await collector.run()

            await asyncio.wait_for(collector.stop(), 0.2)
            with self.assertRaises(asyncio.CancelledError):
                await run_task

            stopped_snapshot = collector.snapshot()
            self.assertEqual(stopped_snapshot["state"], "stopped")
            self.assertIsNone(stopped_snapshot["error"])
            self.assertEqual(stopped_snapshot["subscribed_symbols"], [])

            run_task = asyncio.create_task(collector.run())
            await eventually(lambda: len(provider.requests) == 2)
            self.assertEqual(collector.snapshot()["state"], "running")
            await asyncio.wait_for(collector.stop(), 0.2)
            with self.assertRaises(asyncio.CancelledError):
                await run_task

            self.assertEqual(store.close_attempts, 3)
            self.assertEqual(
                store.closed,
                [
                    ("fake", ("SPY", "QQQ", "SOXX", "AMD")),
                    ("fake", ("SPY", "QQQ", "SOXX", "AMD")),
                ],
            )
            self.assertEqual(provider.close_calls, 2)
        finally:
            if not run_task.done():
                run_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await run_task


if __name__ == "__main__":
    unittest.main()
