from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

from websockets.asyncio.client import connect as websocket_connect

from marketdata.base import ProviderCapabilities
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
        self._stream_task = None
        self._closing = False
        self._normalizer = AlpacaEventNormalizer()
        self._write_lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()

    def capabilities(self):
        reason = None if self._api_key and self._api_secret else "missing_credentials"
        return ProviderCapabilities(
            "alpaca",
            "iex",
            True,
            30,
            False,
            False,
            True,
            False,
            False,
            reason,
        )

    async def stream_events(self, request, emit):
        if not self._api_key or not self._api_secret:
            raise ValueError("Alpaca credentials are required")
        current_task = asyncio.current_task()
        self._stream_task = current_task
        self._closing = False
        if self._desired_symbols is None:
            self._set_desired(request.symbols)
        socket = None
        try:
            self._normalizer.reset()
            async with self._connect(STREAM_URL) as socket:
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
                subscribed = False
                async for raw in socket:
                    messages = self._decode_messages(raw)
                    for payload in messages:
                        if (
                            payload.get("T") == "success"
                            and payload.get("msg") == "authenticated"
                        ):
                            self._authenticated = True
                            await self._apply_subscription()
                            continue
                        if payload.get("T") == "subscription":
                            subscribed = True
                            continue
                        if payload.get("T") == "error":
                            raise RuntimeError("alpaca_stream_error")
                        event = self._normalizer.ingest(
                            payload,
                            datetime.now(timezone.utc),
                        )
                        if event is not None:
                            await emit(event)
                if not self._authenticated or not subscribed:
                    raise RuntimeError("alpaca_stream_closed_before_subscription")
        finally:
            if socket is not None:
                async with self._write_lock:
                    if self._socket is socket:
                        self._clear_connection_state()
            if self._stream_task is current_task:
                self._stream_task = None

    async def update_subscription(self, request):
        self._set_desired(request.symbols)
        if self._authenticated:
            await self._apply_subscription()

    def _set_desired(self, symbols):
        self._desired_symbols = tuple(symbols)

    async def _apply_subscription(self):
        async with self._subscription_lock:
            desired = self._desired_symbols
            change = plan_change(self._symbols, desired)
            if change.subscribe:
                await self._send(
                    {
                        "action": "subscribe",
                        "trades": list(change.subscribe),
                        "quotes": list(change.subscribe),
                    }
                )
            if change.unsubscribe:
                await self._send(
                    {
                        "action": "unsubscribe",
                        "trades": list(change.unsubscribe),
                        "quotes": list(change.unsubscribe),
                    }
                )
                self._normalizer.clear_symbols(change.unsubscribe)
            self._symbols = desired

    async def _send(self, payload):
        async with self._write_lock:
            socket = self._socket
            if socket is None:
                raise RuntimeError("alpaca_stream_not_connected")
            await socket.send(json.dumps(payload))

    async def close(self):
        self._closing = True
        close_task = asyncio.create_task(self._close_socket())
        cancelled = False
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                cancelled = True
        close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close_socket(self):
        stream_task = self._stream_task
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
        async with self._write_lock:
            socket = self._socket
            if socket is None:
                return
            try:
                await socket.close()
            finally:
                if self._socket is socket:
                    self._clear_connection_state()

    def _clear_connection_state(self):
        self._authenticated = False
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
