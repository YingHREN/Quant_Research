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


class FakeConnection:
    def __init__(self, socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *_args):
        return False


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


if __name__ == "__main__":
    unittest.main()
