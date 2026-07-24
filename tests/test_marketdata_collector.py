import asyncio
from datetime import datetime, timezone
import unittest

from marketdata.base import ProviderCapabilities, TradeEvent
from marketdata.collector import IntradayCollector


class FakeStore:
    def __init__(self):
        self.events = []
        self.opened = []
        self.closed = []
        self.lifecycle = []
        self.capabilities = []

    def initialize(self):
        pass

    def record_capabilities(self, capability, at):
        self.capabilities.append((capability, at))

    def write_event(self, event):
        self.events.append(event)
        return True

    def open_subscription(self, provider, symbols, at):
        self.opened.append((provider, symbols))
        self.lifecycle.append(("open", provider, symbols, at))

    def close_subscription(self, provider, symbols, at):
        self.closed.append((provider, symbols))
        self.lifecycle.append(("close", provider, symbols, at))


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

    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
        await asyncio.Event().wait()

    async def update_subscription(self, request):
        self.updated.append(request.symbols)

    async def close(self):
        self.close_calls += 1


async def eventually(predicate, timeout=0.5):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout)


class FailingProvider(FakeProvider):
    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
        raise RuntimeError("sensitive provider detail")


class BlockingUpdateProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.first_update_started = asyncio.Event()
        self.release_first_update = asyncio.Event()
        self.concurrent_updates = 0
        self.max_concurrent_updates = 0
        self.update_cancelled = False

    async def update_subscription(self, request):
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


class SlowCancelUpdateProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.update_started = asyncio.Event()
        self.update_cancellation_started = asyncio.Event()
        self.finish_update_cancellation = asyncio.Event()
        self.update_finished = asyncio.Event()

    async def update_subscription(self, request):
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

    async def update_subscription(self, request):
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

    async def stream_events(self, request, emit):
        raise AssertionError("unavailable provider must not connect")


class RetryOnceProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.second_attempt_started = asyncio.Event()

    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
        if len(self.requests) == 1:
            raise RuntimeError("sensitive stream detail")
        self.second_attempt_started.set()
        await asyncio.Event().wait()


class EmittingProvider(FakeProvider):
    def __init__(self, event):
        super().__init__()
        self.event = event
        self.emitted = asyncio.Event()

    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
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

    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
        if len(self.requests) == 1:
            self.first_stream_started.set()
            await self.fail_first_stream.wait()
            raise RuntimeError("first stream failed")
        self.second_stream_started.set()
        await asyncio.Event().wait()

    async def update_subscription(self, request):
        self.updated.append(request.symbols)
        self.update_started.set()
        try:
            await self.release_update.wait()
        except asyncio.CancelledError:
            self.update_cancelled = True
            raise


class FailUpdateThenReconnectProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.first_stream_started = asyncio.Event()
        self.fail_first_stream = asyncio.Event()
        self.second_stream_started = asyncio.Event()

    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
        if len(self.requests) == 1:
            self.first_stream_started.set()
            await self.fail_first_stream.wait()
            raise RuntimeError("first stream failed")
        self.second_stream_started.set()
        await asyncio.Event().wait()

    async def update_subscription(self, request):
        self.updated.append(request.symbols)
        raise RuntimeError("update failed")


class RestartableProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.stream_releases = []

    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
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

    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
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


class FailOnceUpdateProvider(FakeProvider):
    async def update_subscription(self, request):
        self.updated.append(request.symbols)
        if len(self.updated) == 1:
            raise RuntimeError("first update failed")


class IntradayCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_selection_builds_expected_pool_and_updates_connected_provider(self):
        provider, store = FakeProvider(), FakeStore()
        collector = IntradayCollector(provider, store, retry_delays=(0,))
        collector.set_selection("AMD", ["NVDA", "AVGO"], ["NBIS"])
        task = asyncio.create_task(collector.run())
        await asyncio.sleep(0)
        self.assertEqual(provider.requests[0][:4], ("SPY", "QQQ", "SOXX", "AMD"))

        collector.set_selection("NBIS", ["AMD"], [])
        await asyncio.sleep(0)
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


if __name__ == "__main__":
    unittest.main()
