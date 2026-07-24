from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from marketdata.base import SubscriptionRequest
from marketdata.subscriptions import build_pool, plan_change


class IntradayCollector:
    def __init__(self, provider, store, retry_delays=(1, 2, 5, 10, 30)):
        self._provider = provider
        self._store = store
        self._retry_delays = tuple(retry_delays) or (0,)
        limit = provider.capabilities().max_symbols
        self._desired = SubscriptionRequest(
            build_pool(None, (), (), limit=limit),
            max_symbols=limit,
        )
        self._active = SubscriptionRequest((), max_symbols=limit)
        self._state = "idle"
        self._last_event_received_at = None
        self._disconnect_count = 0
        self._stream_error = None
        self._update_error = None
        self._stop_requested = False
        self._stop_event = asyncio.Event()
        self._update_task = None
        self._provider_closed = False
        self._run_owner = None
        self._cleanup_owner = None
        self._cleanup_completion = None

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def set_selection(self, selected, peers, candidates):
        limit = self._provider.capabilities().max_symbols
        desired = build_pool(selected, peers, candidates, limit=limit)
        self._desired = SubscriptionRequest(desired, max_symbols=limit)
        if self._desired == self._active:
            self._update_error = None
            return
        if self._state == "running" and (
            self._update_task is None or self._update_task.done()
        ):
            self._update_task = asyncio.create_task(self._apply_desired())
            self._update_task.add_done_callback(self._update_finished)

    async def _apply_desired(self):
        try:
            while (
                self._state == "running"
                and not self._stop_requested
                and self._desired != self._active
            ):
                desired = self._desired
                previous = self._active
                change = plan_change(previous.symbols, desired.symbols)
                await self._provider.update_subscription(desired)
                if self._state != "running" or self._stop_requested:
                    return
                changed_at = self._now()
                if change.subscribe:
                    self._store.open_subscription(
                        self._provider.capabilities().provider,
                        change.subscribe,
                        changed_at,
                    )
                if change.unsubscribe:
                    self._store.close_subscription(
                        self._provider.capabilities().provider,
                        change.unsubscribe,
                        changed_at,
                    )
                self._active = desired
                self._update_error = None
        except asyncio.CancelledError:
            raise
        except Exception:
            self._update_error = (
                None
                if self._desired == self._active
                else "provider_error"
            )

    def _update_finished(self, task):
        if self._update_task is task:
            self._update_task = None

    async def _cancel_update_worker(self):
        update_task = self._update_task
        if update_task is None:
            return
        update_task.cancel()
        await asyncio.gather(update_task, return_exceptions=True)
        if self._update_task is update_task:
            self._update_task = None

    def _close_active(self):
        if not self._active.symbols:
            return
        self._store.close_subscription(
            self._provider.capabilities().provider,
            self._active.symbols,
            self._now(),
        )
        self._active = SubscriptionRequest(
            (),
            max_symbols=self._active.max_symbols,
        )

    async def _perform_cleanup(self, completion):
        error = None
        try:
            await self._cancel_update_worker()
            self._close_active()
            if not self._provider_closed:
                self._provider_closed = True
                await self._provider.close()
            self._state = "stopped"
        except BaseException as caught:
            error = caught
        finally:
            if self._cleanup_owner is asyncio.current_task():
                self._cleanup_owner = None
            if not completion.done():
                completion.set_result(error)

    async def _cleanup_from_run(self):
        if self._cleanup_completion is not None:
            return
        completion = asyncio.get_running_loop().create_future()
        self._cleanup_completion = completion
        self._cleanup_owner = asyncio.current_task()
        await self._perform_cleanup(completion)
        error = completion.result()
        if error is not None:
            raise error

    async def _emit(self, event):
        self._store.write_event(event)
        self._last_event_received_at = event.received_ts

    async def run(self):
        current_task = asyncio.current_task()
        if self._run_owner is not None and not self._run_owner.done():
            raise RuntimeError("collector_already_running")
        self._run_owner = current_task
        self._stop_requested = False
        self._stop_event = asyncio.Event()
        self._update_error = None
        self._cleanup_owner = None
        self._cleanup_completion = None
        try:
            await self._run_owned()
        finally:
            if self._run_owner is current_task:
                self._run_owner = None

    async def _run_owned(self):
        self._store.initialize()
        capabilities = self._provider.capabilities()
        self._store.record_capabilities(capabilities, self._now())
        if capabilities.unavailable_reason is not None:
            self._state = "unavailable"
            self._stream_error = capabilities.unavailable_reason
            return

        self._provider_closed = False
        retry_index = 0
        try:
            while not self._stop_requested:
                started_at = self._now()
                self._state = "connecting"
                self._stream_error = None
                self._active = self._desired
                self._update_error = None
                self._store.open_subscription(
                    capabilities.provider,
                    self._active.symbols,
                    started_at,
                )
                try:
                    self._state = "running"
                    await self._provider.stream_events(self._active, self._emit)
                    if not self._stop_requested:
                        raise RuntimeError("provider_stream_ended")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._disconnect_count += 1
                    self._stream_error = "provider_error"
                    self._state = "retrying"
                    await self._cancel_update_worker()
                    self._close_active()
                    delay = self._retry_delays[
                        min(retry_index, len(self._retry_delays) - 1)
                    ]
                    retry_index += 1
                    if delay <= 0:
                        await asyncio.sleep(0)
                    else:
                        try:
                            await asyncio.wait_for(self._stop_event.wait(), delay)
                        except asyncio.TimeoutError:
                            pass
        finally:
            await self._cleanup_from_run()

    async def stop(self):
        self._stop_requested = True
        self._stop_event.set()
        completion = self._cleanup_completion
        if completion is None:
            completion = asyncio.get_running_loop().create_future()
            self._cleanup_completion = completion
            cleanup_task = asyncio.create_task(
                self._perform_cleanup(completion)
            )
            self._cleanup_owner = cleanup_task
        cancelled = False
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                cancelled = True
        error = completion.result()
        if error is not None:
            raise error
        if cancelled:
            raise asyncio.CancelledError

    def snapshot(self):
        capabilities = self._provider.capabilities()
        return {
            "state": self._state,
            "provider": capabilities.provider,
            "coverage": capabilities.coverage,
            "subscribed_symbols": list(self._active.symbols),
            "desired_symbols": list(self._desired.symbols),
            "last_event_received_at": (
                None
                if self._last_event_received_at is None
                else self._last_event_received_at.isoformat()
            ),
            "disconnect_count": self._disconnect_count,
            "error": self._stream_error or self._update_error,
        }
