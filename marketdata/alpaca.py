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
        self._desired_symbols = ()
        self._authenticated = False
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
        self._desired_symbols = tuple(request.symbols)
        async with self._connect(STREAM_URL) as socket:
            self._socket = socket
            try:
                await self._send(
                    {
                        "action": "auth",
                        "key": self._api_key,
                        "secret": self._api_secret,
                    }
                )
                subscribed = False
                async for raw in socket:
                    messages = json.loads(raw)
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
                self._authenticated = False
                self._socket = None
                self._symbols = ()

    async def update_subscription(self, request):
        self._desired_symbols = tuple(request.symbols)
        if self._authenticated:
            await self._apply_subscription()

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
            self._symbols = desired

    async def _send(self, payload):
        if self._socket is None:
            raise RuntimeError("alpaca_stream_not_connected")
        async with self._write_lock:
            await self._socket.send(json.dumps(payload))

    async def close(self):
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()
