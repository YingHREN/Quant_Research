import asyncio
from contextlib import redirect_stderr
import io
import json
import unittest

from marketdata.alpaca import AlpacaIEXProvider
from marketdata.base import SubscriptionRequest, TradeEvent


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []
        self.closed = False

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return json.dumps(next(self.messages))
        except StopIteration:
            raise StopAsyncIteration


class BlockingFakeSocket(FakeSocket):
    def __init__(self, messages):
        super().__init__(messages)
        self.waiting = asyncio.Event()
        self.finish = asyncio.Event()

    async def __anext__(self):
        try:
            return await super().__anext__()
        except StopAsyncIteration:
            self.waiting.set()
            await self.finish.wait()
            raise


class AuthGatedFakeSocket(FakeSocket):
    def __init__(self, messages):
        super().__init__(messages)
        self.auth_sent = asyncio.Event()
        self.release_messages = asyncio.Event()

    async def send(self, value):
        await super().send(value)
        if self.sent[-1].get("action") == "auth":
            self.auth_sent.set()

    async def __anext__(self):
        await self.release_messages.wait()
        return await super().__anext__()


class QueueFakeSocket(FakeSocket):
    END = object()

    def __init__(self):
        super().__init__(())
        self.queue = asyncio.Queue()

    async def feed(self, message):
        await self.queue.put(message)

    async def finish(self):
        await self.queue.put(self.END)

    async def __anext__(self):
        message = await self.queue.get()
        if message is self.END:
            raise StopAsyncIteration
        return json.dumps(message)


class RawFakeSocket(FakeSocket):
    def __init__(self, messages):
        super().__init__(())
        self.messages = iter(messages)

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration


class SlowSendSocket(FakeSocket):
    def __init__(self):
        super().__init__(())
        self.send_started = asyncio.Event()
        self.finish_send = asyncio.Event()
        self.close_started = asyncio.Event()

    async def send(self, value):
        self.send_started.set()
        await self.finish_send.wait()
        await super().send(value)

    async def close(self):
        self.close_started.set()
        await super().close()


class SlowCloseSocket(FakeSocket):
    def __init__(self):
        super().__init__(())
        self.close_started = asyncio.Event()
        self.finish_close = asyncio.Event()
        self.interrupted = False

    async def close(self):
        self.close_started.set()
        try:
            await self.finish_close.wait()
        except asyncio.CancelledError:
            self.interrupted = True
            raise
        self.closed = True


class FakeConnection:
    def __init__(self, socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *_args):
        return False


class SlowConnection(FakeConnection):
    def __init__(self, socket):
        super().__init__(socket)
        self.enter_started = asyncio.Event()
        self.enter_cancelled = False

    async def __aenter__(self):
        self.enter_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.enter_cancelled = True
            raise


class AlpacaIEXProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_auth_subscribe_normalize_and_emit(self):
        socket = FakeSocket([
            [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}],
            [{"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00Z",
              "p": 150, "s": 10, "i": 1}],
        ])
        provider = AlpacaIEXProvider(
            "key", "secret", connect=lambda _url: FakeConnection(socket)
        )
        events = []

        async def emit(event):
            events.append(event)

        await provider.stream_events(SubscriptionRequest(("AMD",)), emit)

        self.assertEqual(
            socket.sent[0],
            {"action": "auth", "key": "key", "secret": "secret"},
        )
        self.assertEqual(
            socket.sent[1],
            {"action": "subscribe", "trades": ["AMD"], "quotes": ["AMD"]},
        )
        self.assertIsInstance(events[0], TradeEvent)
        self.assertEqual(provider.capabilities().coverage, "iex")

    async def test_missing_credentials_fail_before_connect(self):
        connect_calls = []
        provider = AlpacaIEXProvider(
            "",
            "",
            connect=lambda url: connect_calls.append(url),
        )

        with self.assertRaisesRegex(ValueError, "credentials"):
            await provider.stream_events(
                SubscriptionRequest(("AMD",)),
                lambda _event: None,
            )

        self.assertEqual(connect_calls, [])

    async def test_update_subscription_sends_additions_before_removals(self):
        socket = BlockingFakeSocket([
            [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}],
        ])
        provider = AlpacaIEXProvider(
            "key",
            "secret",
            connect=lambda _url: FakeConnection(socket),
        )

        async def emit(_event):
            pass

        stream = asyncio.create_task(
            provider.stream_events(SubscriptionRequest(("AMD",)), emit)
        )
        await socket.waiting.wait()
        await provider.update_subscription(SubscriptionRequest(("NVDA",)))
        socket.finish.set()
        await stream

        self.assertEqual(
            socket.sent[2:],
            [
                {"action": "subscribe", "trades": ["NVDA"], "quotes": ["NVDA"]},
                {"action": "unsubscribe", "trades": ["AMD"], "quotes": ["AMD"]},
            ],
        )

    async def test_authentication_error_does_not_expose_secret(self):
        secret = "super-sensitive-secret"
        socket = FakeSocket([
            [{"T": "error", "code": 402, "msg": f"bad secret {secret}"}],
        ])
        provider = AlpacaIEXProvider(
            "key",
            secret,
            connect=lambda _url: FakeConnection(socket),
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            with self.assertRaises(RuntimeError) as raised:
                await provider.stream_events(
                    SubscriptionRequest(("AMD",)),
                    lambda _event: None,
                )

        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, stderr.getvalue())

    async def test_update_before_auth_waits_and_replaces_initial_request(self):
        socket = AuthGatedFakeSocket([
            [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", "trades": ["NVDA"], "quotes": ["NVDA"]}],
        ])
        provider = AlpacaIEXProvider(
            "key",
            "secret",
            connect=lambda _url: FakeConnection(socket),
        )

        async def emit(_event):
            pass

        stream = asyncio.create_task(
            provider.stream_events(SubscriptionRequest(("AMD",)), emit)
        )
        await socket.auth_sent.wait()
        await provider.update_subscription(SubscriptionRequest(("NVDA",)))

        self.assertEqual(
            socket.sent,
            [{"action": "auth", "key": "key", "secret": "secret"}],
        )

        socket.release_messages.set()
        await stream
        self.assertEqual(
            socket.sent[1:],
            [{"action": "subscribe", "trades": ["NVDA"], "quotes": ["NVDA"]}],
        )

    async def test_immediate_update_after_stream_creation_wins_startup_race(self):
        socket = AuthGatedFakeSocket([
            [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", "trades": ["NVDA"], "quotes": ["NVDA"]}],
        ])
        provider = AlpacaIEXProvider(
            "key",
            "secret",
            connect=lambda _url: FakeConnection(socket),
        )

        async def emit(_event):
            pass

        stream = asyncio.create_task(
            provider.stream_events(SubscriptionRequest(("AMD",)), emit)
        )
        await provider.update_subscription(SubscriptionRequest(("NVDA",)))
        socket.release_messages.set()
        await stream

        self.assertEqual(
            socket.sent[1],
            {"action": "subscribe", "trades": ["NVDA"], "quotes": ["NVDA"]},
        )

    async def test_reconnect_resubscribes_same_symbols_on_new_socket(self):
        sockets = [
            FakeSocket([
                [{"T": "success", "msg": "authenticated"}],
                [{"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}],
            ]),
            FakeSocket([
                [{"T": "success", "msg": "authenticated"}],
                [{"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}],
            ]),
        ]
        connections = iter(sockets)
        provider = AlpacaIEXProvider(
            "key",
            "secret",
            connect=lambda _url: FakeConnection(next(connections)),
        )

        async def emit(_event):
            pass

        request = SubscriptionRequest(("AMD",))
        await provider.stream_events(request, emit)
        await provider.stream_events(request, emit)

        expected = [
            {"action": "auth", "key": "key", "secret": "secret"},
            {"action": "subscribe", "trades": ["AMD"], "quotes": ["AMD"]},
        ]
        self.assertEqual(sockets[0].sent, expected)
        self.assertEqual(sockets[1].sent, expected)

    async def test_reconnect_resets_direction_state(self):
        sockets = [
            FakeSocket([
                [{"T": "success", "msg": "authenticated"}],
                [{"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}],
                [{"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00Z",
                  "bp": 100, "bs": 1, "ap": 101, "as": 1}],
            ]),
            FakeSocket([
                [{"T": "success", "msg": "authenticated"}],
                [{"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}],
                [{"T": "t", "S": "AMD", "t": "2026-07-24T14:30:01Z",
                  "p": 102, "s": 1}],
            ]),
        ]
        connections = iter(sockets)
        provider = AlpacaIEXProvider(
            "key",
            "secret",
            connect=lambda _url: FakeConnection(next(connections)),
        )
        events = []

        async def emit(event):
            events.append(event)

        request = SubscriptionRequest(("AMD",))
        await provider.stream_events(request, emit)
        await provider.stream_events(request, emit)

        trade = events[-1]
        self.assertIsInstance(trade, TradeEvent)
        self.assertEqual(
            (trade.direction, trade.direction_source),
            ("unknown", "unknown"),
        )

    async def test_unsubscribe_clears_symbol_direction_state(self):
        socket = QueueFakeSocket()
        provider = AlpacaIEXProvider(
            "key",
            "secret",
            connect=lambda _url: FakeConnection(socket),
        )
        quote_emitted = asyncio.Event()
        events = []

        async def emit(event):
            events.append(event)
            if event.symbol == "AMD":
                quote_emitted.set()

        stream = asyncio.create_task(
            provider.stream_events(SubscriptionRequest(("AMD",)), emit)
        )
        await socket.feed([{"T": "success", "msg": "authenticated"}])
        await socket.feed([
            {"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}
        ])
        await socket.feed([
            {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00Z",
             "bp": 100, "bs": 1, "ap": 101, "as": 1}
        ])
        await quote_emitted.wait()

        await provider.update_subscription(SubscriptionRequest(("NVDA",)))
        await socket.feed([
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:01Z",
             "p": 102, "s": 1}
        ])
        await socket.finish()
        await stream

        trade = events[-1]
        self.assertIsInstance(trade, TradeEvent)
        self.assertEqual(
            (trade.direction, trade.direction_source),
            ("unknown", "unknown"),
        )

    async def test_close_waits_for_in_flight_send(self):
        socket = SlowSendSocket()
        provider = AlpacaIEXProvider("key", "secret")
        provider._socket = socket

        send = asyncio.create_task(provider._send({"action": "subscribe"}))
        await socket.send_started.wait()
        close = asyncio.create_task(provider.close())
        await asyncio.sleep(0)

        self.assertFalse(socket.close_started.is_set())
        socket.finish_send.set()
        await send
        await close
        self.assertTrue(socket.closed)
        self.assertIsNone(provider._socket)

    async def test_cancelled_close_finishes_socket_close_then_propagates(self):
        socket = SlowCloseSocket()
        provider = AlpacaIEXProvider("key", "secret")
        provider._socket = socket

        close = asyncio.create_task(provider.close())
        await socket.close_started.wait()
        close.cancel()
        await asyncio.sleep(0)

        self.assertFalse(close.done())
        socket.finish_close.set()
        with self.assertRaises(asyncio.CancelledError):
            await close
        self.assertTrue(socket.closed)
        self.assertFalse(socket.interrupted)
        self.assertIsNone(provider._socket)

    async def test_cancelled_close_waiting_for_send_still_closes_socket(self):
        socket = SlowSendSocket()
        provider = AlpacaIEXProvider("key", "secret")
        provider._socket = socket

        send = asyncio.create_task(provider._send({"action": "subscribe"}))
        await socket.send_started.wait()
        close = asyncio.create_task(provider.close())
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)

        self.assertFalse(close.done())
        socket.finish_send.set()
        await send
        with self.assertRaises(asyncio.CancelledError):
            await close
        self.assertTrue(socket.closed)
        self.assertIsNone(provider._socket)

    async def test_close_cancels_and_waits_for_pending_connection(self):
        socket = FakeSocket(())
        connection = SlowConnection(socket)
        provider = AlpacaIEXProvider(
            "key",
            "secret",
            connect=lambda _url: connection,
        )

        async def emit(_event):
            pass

        stream = asyncio.create_task(
            provider.stream_events(SubscriptionRequest(("AMD",)), emit)
        )
        await connection.enter_started.wait()
        try:
            await provider.close()
            self.assertTrue(stream.done())
            self.assertTrue(connection.enter_cancelled)
            self.assertEqual(socket.sent, [])
            self.assertIsNone(provider._socket)
        finally:
            if not stream.done():
                stream.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stream

    async def test_invalid_frames_raise_stable_sanitized_protocol_error(self):
        invalid_frames = (
            "{not-json",
            json.dumps({"T": "success"}),
            json.dumps(None),
            json.dumps([1]),
            json.dumps([None]),
        )

        async def emit(_event):
            pass

        for raw in invalid_frames:
            with self.subTest(raw=raw):
                socket = RawFakeSocket((raw,))
                provider = AlpacaIEXProvider(
                    "key",
                    "secret",
                    connect=lambda _url, socket=socket: FakeConnection(socket),
                )
                with self.assertRaises(RuntimeError) as raised:
                    await provider.stream_events(
                        SubscriptionRequest(("AMD",)),
                        emit,
                    )
                self.assertEqual(str(raised.exception), "alpaca_stream_error")


if __name__ == "__main__":
    unittest.main()
