from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json

from websockets.asyncio.client import connect as websocket_connect

from marketdata.base import (
    ProviderCapabilities,
    SubscriptionConfirmation,
    SubscriptionRequest,
)
from marketdata.normalization import AlpacaEventNormalizer
from marketdata.subscriptions import plan_change


STREAM_URL = "wss://stream.data.alpaca.markets/v2/iex"


class AlpacaIEXProvider:
    def __init__(self, api_key, api_secret, connect=None):
        self._api_key = api_key
        self._api_secret = api_secret
        self._connect = websocket_connect if connect is None else connect
        self._socket = None
        self._symbols = ()
        self._desired_symbols = None
        self._authenticated = False
        self._initial_confirmed = False
        self._initial_expected = ()
        self._pending_ack = None
        self._stream_task = None
        self._closing = False
        self._normalizer = AlpacaEventNormalizer()
        self._write_lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    def capabilities(self):
        reason = (
            None
            if self._api_key and self._api_secret
            else "missing_credentials"
        )
        return ProviderCapabilities(
            "alpaca",
            "iex",
            True,
            30,
            False,
            False,
            False,
            False,
            False,
            reason,
        )

    async def stream_events(self, request, emit, on_confirmed=None):
        if not self._api_key or not self._api_secret:
            raise ValueError("Alpaca credentials are required")
        current_task = asyncio.current_task()
        async with self._lifecycle_lock:
            if self._stream_task is not None and not self._stream_task.done():
                raise RuntimeError("alpaca_stream_already_running")
            self._stream_task = current_task
            self._closing = False
        if self._desired_symbols is None:
            self._set_desired(request.symbols)

        socket = None
        try:
            self._normalizer.reset()
            async with self._connect(STREAM_URL) as connected_socket:
                socket = connected_socket
                if self._closing:
                    raise asyncio.CancelledError
                async with self._write_lock:
                    self._socket = socket
                await self._send(
                    {
                        "action": "auth",
                        "key": self._api_key,
                        "secret": self._api_secret,
                    }
                )
                async for raw in socket:
                    messages = self._decode_messages(raw)
                    for payload in messages:
                        await self._handle_payload(
                            payload,
                            emit,
                            on_confirmed,
                        )
                        if self._closing:
                            return
                if not self._authenticated or not self._initial_confirmed:
                    raise RuntimeError(
                        "alpaca_stream_closed_before_subscription"
                    )
        finally:
            pending = self._pending_ack
            if pending is not None and not pending[0].done():
                pending[0].set_exception(
                    RuntimeError(
                        "alpaca_stream_closed_before_subscription"
                    )
                )
            async with self._write_lock:
                if socket is None or self._socket is socket:
                    self._clear_connection_state()
            async with self._lifecycle_lock:
                if self._stream_task is current_task:
                    self._stream_task = None

    async def _handle_payload(self, payload, emit, on_confirmed):
        message_type = payload.get("T")
        if (
            message_type == "success"
            and payload.get("msg") == "authenticated"
        ):
            if self._authenticated:
                return
            self._authenticated = True
            self._initial_expected = tuple(self._desired_symbols or ())
            await self._send_subscription_action(
                "subscribe",
                self._initial_expected,
            )
            return

        if message_type == "subscription":
            pending = self._pending_ack
            if pending is not None:
                future, expected = pending
                try:
                    confirmation = self._validate_ack(payload, expected)
                except RuntimeError as error:
                    if not future.done():
                        future.set_exception(error)
                    raise
                if not future.done():
                    future.set_result(confirmation)
                return
            if not self._authenticated:
                raise RuntimeError("alpaca_subscription_mismatch")
            expected = (
                self._initial_expected
                if not self._initial_confirmed
                else self._symbols
            )
            confirmation = self._validate_ack(payload, expected)
            if not self._initial_confirmed:
                self._symbols = confirmation.symbols
                self._initial_confirmed = True
                if on_confirmed is not None:
                    result = on_confirmed(confirmation)
                    if inspect.isawaitable(result):
                        await result
            return

        if message_type == "error":
            if self._pending_ack is not None:
                error = RuntimeError("alpaca_subscription_error")
                future = self._pending_ack[0]
                if not future.done():
                    future.set_exception(error)
                raise error
            if self._authenticated and not self._initial_confirmed:
                raise RuntimeError("alpaca_subscription_error")
            if not self._authenticated:
                raise RuntimeError("alpaca_authentication_error")
            raise RuntimeError("alpaca_stream_error")

        if not self._initial_confirmed:
            self._normalizer.ingest(
                payload,
                datetime.now(timezone.utc),
            )
            return
        event = self._normalizer.ingest(
            payload,
            datetime.now(timezone.utc),
        )
        if event is not None:
            await emit(event)

    async def update_subscription(self, request):
        self._set_desired(request.symbols)
        if not self._authenticated or not self._initial_confirmed:
            return None
        return await self._apply_subscription()

    def _set_desired(self, symbols):
        self._desired_symbols = tuple(symbols)

    async def _apply_subscription(self):
        async with self._subscription_lock:
            desired = tuple(self._desired_symbols or ())
            change = plan_change(self._symbols, desired)
            if change.subscribe:
                expected_after_add = tuple(
                    dict.fromkeys(self._symbols + change.subscribe)
                )
                confirmation = await self._send_and_wait_for_ack(
                    "subscribe",
                    change.subscribe,
                    expected_after_add,
                )
                self._symbols = confirmation.symbols
            if change.unsubscribe:
                expected_after_remove = tuple(
                    symbol
                    for symbol in self._symbols
                    if symbol not in set(change.unsubscribe)
                )
                confirmation = await self._send_and_wait_for_ack(
                    "unsubscribe",
                    change.unsubscribe,
                    expected_after_remove,
                )
                self._symbols = confirmation.symbols
                self._normalizer.clear_symbols(change.unsubscribe)
            final_confirmation = SubscriptionConfirmation(
                desired,
                desired,
            )
            self._symbols = final_confirmation.symbols
            return final_confirmation

    async def _send_and_wait_for_ack(
        self,
        action,
        changed_symbols,
        expected,
    ):
        if self._pending_ack is not None:
            raise RuntimeError("alpaca_subscription_update_in_progress")
        future = asyncio.get_running_loop().create_future()
        pending = (future, tuple(expected))
        self._pending_ack = pending
        try:
            await self._send_subscription_action(action, changed_symbols)
            return await future
        finally:
            if self._pending_ack is pending:
                self._pending_ack = None

    async def _send_subscription_action(self, action, symbols):
        await self._send(
            {
                "action": action,
                "trades": list(symbols),
                "quotes": list(symbols),
            }
        )

    @staticmethod
    def _validate_ack(payload, expected):
        trades = payload.get("trades")
        quotes = payload.get("quotes")
        if not isinstance(trades, list) or not isinstance(quotes, list):
            raise RuntimeError("alpaca_subscription_mismatch")
        try:
            normalized_trades = SubscriptionRequest(trades).symbols
            normalized_quotes = SubscriptionRequest(quotes).symbols
        except (TypeError, ValueError):
            raise RuntimeError("alpaca_subscription_mismatch") from None
        expected_tuple = tuple(expected)
        expected_set = set(expected_tuple)
        if (
            len(normalized_trades) != len(trades)
            or len(normalized_quotes) != len(quotes)
            or set(normalized_trades) != expected_set
            or set(normalized_quotes) != expected_set
        ):
            raise RuntimeError("alpaca_subscription_mismatch")
        return SubscriptionConfirmation(expected_tuple, expected_tuple)

    async def _send(self, payload):
        async with self._write_lock:
            socket = self._socket
            if socket is None:
                raise RuntimeError("alpaca_stream_not_connected")
            await socket.send(json.dumps(payload))

    async def close(self):
        caller_task = asyncio.current_task()
        async with self._lifecycle_lock:
            self._closing = True
        close_task = asyncio.create_task(
            self._close_socket(caller_task)
        )
        cancelled = False
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                cancelled = True
        close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close_socket(self, caller_task):
        async with self._lifecycle_lock:
            stream_task = self._stream_task
        async with self._write_lock:
            socket = self._socket
            if socket is not None:
                try:
                    await socket.close()
                finally:
                    if self._socket is socket:
                        self._clear_connection_state()
            else:
                self._clear_connection_state()
        if (
            stream_task is not None
            and stream_task is not caller_task
            and not stream_task.done()
        ):
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass

    def _clear_connection_state(self):
        pending = self._pending_ack
        if pending is not None and not pending[0].done():
            pending[0].set_exception(
                RuntimeError(
                    "alpaca_stream_closed_before_subscription"
                )
            )
        self._pending_ack = None
        self._authenticated = False
        self._initial_confirmed = False
        self._initial_expected = ()
        self._socket = None
        self._symbols = ()
        self._desired_symbols = None

    @staticmethod
    def _decode_messages(raw):
        try:
            messages = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            raise RuntimeError("alpaca_stream_error") from None
        if not isinstance(messages, list) or any(
            not isinstance(payload, dict) for payload in messages
        ):
            raise RuntimeError("alpaca_stream_error")
        return messages
