from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import json
from math import isfinite

from websockets.asyncio.client import connect as websocket_connect

from marketdata.base import (
    ProviderConnectionError,
    ProviderCapabilities,
    SubscriptionConfirmation,
    SubscriptionRequest,
)
from marketdata.normalization import AlpacaEventNormalizer
from marketdata.subscriptions import plan_change


STREAM_URL = "wss://stream.data.alpaca.markets/v2/iex"
DEFAULT_ACK_TIMEOUT_SECONDS = 10.0


class AlpacaIEXProvider:
    def __init__(
        self,
        api_key,
        api_secret,
        connect=None,
        *,
        ack_timeout_seconds=DEFAULT_ACK_TIMEOUT_SECONDS,
    ):
        if (
            isinstance(ack_timeout_seconds, bool)
            or not isinstance(ack_timeout_seconds, (int, float))
            or not isfinite(ack_timeout_seconds)
            or ack_timeout_seconds <= 0
        ):
            raise ValueError("ack_timeout_seconds must be positive")
        self._api_key = api_key
        self._api_secret = api_secret
        self._connect = websocket_connect if connect is None else connect
        self._ack_timeout_seconds = float(ack_timeout_seconds)
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
                clock = asyncio.get_running_loop().time
                authentication_deadline = (
                    clock() + self._ack_timeout_seconds
                )
                initial_subscription_deadline = None
                iterator = socket.__aiter__()
                while True:
                    timeout_code = None
                    deadline = None
                    if not self._authenticated:
                        timeout_code = "alpaca_authentication_timeout"
                        deadline = authentication_deadline
                    elif not self._initial_confirmed:
                        timeout_code = "alpaca_subscription_timeout"
                        if initial_subscription_deadline is None:
                            initial_subscription_deadline = (
                                clock() + self._ack_timeout_seconds
                            )
                        deadline = initial_subscription_deadline
                    try:
                        if deadline is None:
                            raw = await iterator.__anext__()
                        else:
                            remaining = deadline - clock()
                            if remaining <= 0:
                                raise asyncio.TimeoutError
                            raw = await asyncio.wait_for(
                                iterator.__anext__(),
                                timeout=remaining,
                            )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        raise RuntimeError(timeout_code) from None
                    messages = self._decode_messages(raw)
                    for payload in messages:
                        was_authenticated = self._authenticated
                        await self._handle_payload(
                            payload,
                            emit,
                            on_confirmed,
                        )
                        if (
                            not was_authenticated
                            and self._authenticated
                            and not self._initial_confirmed
                            and initial_subscription_deadline is None
                        ):
                            initial_subscription_deadline = (
                                clock() + self._ack_timeout_seconds
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
                future, expected, apply_confirmation = pending
                if future.done():
                    return
                try:
                    confirmation = self._validate_ack(payload, expected)
                    await apply_confirmation(confirmation)
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
                await self._notify_confirmation(
                    on_confirmed,
                    confirmation,
                )
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

    async def update_subscription(self, request, on_confirmed=None):
        self._set_desired(request.symbols)
        if not self._authenticated or not self._initial_confirmed:
            return None
        return await self._apply_subscription(on_confirmed)

    def _set_desired(self, symbols):
        self._desired_symbols = tuple(symbols)

    async def _apply_subscription(self, on_confirmed):
        async with self._subscription_lock:
            desired = tuple(self._desired_symbols or ())
            change = plan_change(self._symbols, desired)
            free_slots = (
                self.capabilities().max_symbols - len(self._symbols)
            )
            early_remove_count = max(
                0,
                len(change.subscribe) - free_slots,
            )
            early_removals = change.unsubscribe[:early_remove_count]
            later_removals = change.unsubscribe[early_remove_count:]

            if early_removals:
                await self._apply_acknowledged_change(
                    "unsubscribe",
                    early_removals,
                    on_confirmed,
                )
            if change.subscribe:
                await self._apply_acknowledged_change(
                    "subscribe",
                    change.subscribe,
                    on_confirmed,
                )
            if later_removals:
                await self._apply_acknowledged_change(
                    "unsubscribe",
                    later_removals,
                    on_confirmed,
                )
            if set(self._symbols) != set(desired):
                raise RuntimeError("alpaca_subscription_mismatch")
            final_confirmation = SubscriptionConfirmation(
                desired,
                desired,
            )
            self._symbols = final_confirmation.symbols
            return final_confirmation

    async def _apply_acknowledged_change(
        self,
        action,
        changed_symbols,
        on_confirmed,
    ):
        if action == "subscribe":
            expected = tuple(
                dict.fromkeys(self._symbols + tuple(changed_symbols))
            )
        else:
            removed = set(changed_symbols)
            expected = tuple(
                symbol
                for symbol in self._symbols
                if symbol not in removed
            )

        async def apply_confirmation(confirmation):
            self._symbols = confirmation.symbols
            if action == "unsubscribe":
                self._normalizer.clear_symbols(changed_symbols)
            await self._notify_confirmation(on_confirmed, confirmation)

        await self._send_and_wait_for_ack(
            action,
            changed_symbols,
            expected,
            apply_confirmation,
        )

    @staticmethod
    async def _notify_confirmation(callback, confirmation):
        if callback is None:
            return
        result = callback(confirmation)
        if inspect.isawaitable(result):
            await result

    async def _send_and_wait_for_ack(
        self,
        action,
        changed_symbols,
        expected,
        apply_confirmation,
    ):
        if self._pending_ack is not None:
            raise RuntimeError("alpaca_subscription_update_in_progress")
        future = asyncio.get_running_loop().create_future()
        pending = (future, tuple(expected), apply_confirmation)
        self._pending_ack = pending
        try:
            await self._send_subscription_action(action, changed_symbols)
            try:
                return await asyncio.wait_for(
                    future,
                    timeout=self._ack_timeout_seconds,
                )
            except asyncio.TimeoutError:
                raise ProviderConnectionError(
                    "alpaca_subscription_timeout"
                ) from None
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
