from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import uuid

from marketdata.base import SubscriptionRequest
from marketdata.subscriptions import build_pool, plan_change


DEFAULT_EVENT_QUEUE_SIZE = 4096
DEFAULT_EVENT_BATCH_SIZE = 128
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 5.0
DEFAULT_STALE_AFTER_SECONDS = 30
_WRITER_STOP = object()


class _CollectorStorageError(RuntimeError):
    pass


class IntradayCollector:
    def __init__(
        self,
        provider,
        store,
        retry_delays=(1, 2, 5, 10, 30),
        *,
        queue_size=DEFAULT_EVENT_QUEUE_SIZE,
        batch_size=DEFAULT_EVENT_BATCH_SIZE,
        heartbeat_interval=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS,
    ):
        if (
            isinstance(queue_size, bool)
            or not isinstance(queue_size, int)
            or queue_size <= 0
        ):
            raise ValueError("queue_size must be a positive integer")
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size <= 0
        ):
            raise ValueError("batch_size must be a positive integer")
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        self._provider = provider
        self._store = store
        self._retry_delays = tuple(retry_delays) or (0,)
        self._queue_size = queue_size
        self._batch_size = batch_size
        self._heartbeat_interval = float(heartbeat_interval)
        self._stale_after_seconds = stale_after_seconds
        limit = provider.capabilities().max_symbols
        self._desired = SubscriptionRequest(
            build_pool(None, (), (), limit=limit),
            max_symbols=limit,
        )
        self._active = SubscriptionRequest((), max_symbols=limit)
        self._state = "idle"
        self._last_event_received_at = None
        self._heartbeat_at = None
        self._disconnect_count = 0
        self._stream_error = None
        self._update_error = None
        self._cleanup_error = None
        self._storage_error = None
        self._stop_requested = False
        self._stop_event = asyncio.Event()
        self._update_task = None
        self._provider_closed = False
        self._run_owner = None
        self._cleanup_owner = None
        self._cleanup_completion = None
        self._session_id = None
        self._event_queue = None
        self._writer_task = None
        self._storage_failure_event = None
        self._heartbeat_task = None
        self._queue_high_water = 0
        self._dropped_event_count = 0
        self._undrained_event_count = 0
        self._accepting_events = False

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
            self._update_task = asyncio.create_task(
                self._apply_desired()
            )
            self._update_task.add_done_callback(self._update_finished)

    async def _apply_desired(self):
        try:
            while (
                self._state == "running"
                and not self._stop_requested
                and self._desired != self._active
            ):
                desired = self._desired
                confirmation = await self._provider.update_subscription(
                    desired
                )
                if confirmation is None:
                    raise RuntimeError("provider_confirmation_missing")
                if self._state != "running" or self._stop_requested:
                    return
                self._record_confirmation(confirmation, desired)
                self._update_error = None
                self._publish_status()
        except asyncio.CancelledError:
            raise
        except _CollectorStorageError:
            self._mark_storage_error()
        except Exception:
            self._update_error = (
                None
                if self._desired == self._active
                else "provider_error"
            )
            self._publish_status_safely()

    def _record_confirmation(self, confirmation, desired):
        confirmed = SubscriptionRequest(
            confirmation.symbols,
            max_symbols=desired.max_symbols,
        )
        if set(confirmed.symbols) != set(desired.symbols):
            raise RuntimeError("provider_confirmation_mismatch")
        previous = self._active
        change = plan_change(previous.symbols, confirmed.symbols)
        changed_at = self._now()
        try:
            if change.subscribe:
                self._store.open_subscription(
                    self._provider.capabilities().provider,
                    change.subscribe,
                    changed_at,
                    session_id=self._session_id,
                )
            if change.unsubscribe:
                self._store.close_subscription(
                    self._provider.capabilities().provider,
                    change.unsubscribe,
                    changed_at,
                    session_id=self._session_id,
                )
        except Exception as error:
            raise _CollectorStorageError from error
        self._active = confirmed

    async def _initial_confirmation(self, confirmation):
        if self._stop_requested:
            return
        desired = SubscriptionRequest(
            confirmation.symbols,
            max_symbols=self._desired.max_symbols,
        )
        self._record_confirmation(confirmation, desired)
        self._state = "running"
        self._stream_error = None
        self._update_error = None
        self._accepting_events = True
        self._publish_status()
        if self._desired != self._active and (
            self._update_task is None or self._update_task.done()
        ):
            self._update_task = asyncio.create_task(
                self._apply_desired()
            )
            self._update_task.add_done_callback(self._update_finished)

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
            session_id=self._session_id,
        )
        self._active = SubscriptionRequest(
            (),
            max_symbols=self._active.max_symbols,
        )

    async def _perform_cleanup(self, completion):
        result = None
        try:
            await self._cancel_update_worker()
            self._accepting_events = False
            await self._stop_writer()
            self._close_active()
            if not self._provider_closed:
                await self._provider.close()
                self._provider_closed = True
            await self._stop_heartbeat()
            if self._storage_error is None:
                self._state = "stopped"
                self._cleanup_error = None
                self._stream_error = None
                self._update_error = None
            else:
                self._state = "collector_error"
            self._publish_status()
        except asyncio.CancelledError:
            result = "cancelled"
            self._state = "cleanup_failed"
            self._cleanup_error = "provider_error"
            self._publish_status_safely()
        except _CollectorStorageError:
            result = "storage_error"
            self._mark_storage_error()
        except Exception:
            result = "provider_error"
            self._state = "cleanup_failed"
            self._cleanup_error = "provider_error"
            self._publish_status_safely()
        finally:
            if self._cleanup_owner is asyncio.current_task():
                self._cleanup_owner = None
            if not completion.done():
                completion.set_result(result)

    @staticmethod
    def _raise_cleanup_result(result):
        if result == "cancelled":
            raise asyncio.CancelledError
        if result == "storage_error":
            raise RuntimeError("collector_storage_failed")
        if result is not None:
            raise RuntimeError("collector_cleanup_failed")

    async def _cleanup_from_run(self):
        if self._cleanup_completion is not None:
            return
        completion = asyncio.get_running_loop().create_future()
        self._cleanup_completion = completion
        self._cleanup_owner = asyncio.current_task()
        await self._perform_cleanup(completion)
        self._raise_cleanup_result(completion.result())

    async def _emit(self, event):
        if (
            not self._accepting_events
            or self._event_queue is None
            or self._storage_error is not None
        ):
            raise RuntimeError("collector_not_accepting_events")
        await self._event_queue.put(event)
        self._queue_high_water = max(
            self._queue_high_water,
            self._event_queue.qsize(),
        )

    async def _writer_loop(self):
        queue = self._event_queue
        while True:
            first = await queue.get()
            if first is _WRITER_STOP:
                queue.task_done()
                return
            batch = [first]
            stop_after_batch = False
            while len(batch) < self._batch_size:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is _WRITER_STOP:
                    queue.task_done()
                    stop_after_batch = True
                    break
                batch.append(item)
            try:
                await asyncio.to_thread(
                    self._store.write_events,
                    tuple(batch),
                )
            except Exception:
                self._undrained_event_count = len(batch) + queue.qsize()
                self._mark_storage_error()
                return
            for _event in batch:
                queue.task_done()
            latest = max(event.received_ts for event in batch)
            if (
                self._last_event_received_at is None
                or latest > self._last_event_received_at
            ):
                self._last_event_received_at = latest
            try:
                await asyncio.to_thread(self._publish_status)
            except _CollectorStorageError:
                self._undrained_event_count = queue.qsize()
                self._mark_storage_error()
                return
            if stop_after_batch:
                return

    async def _stop_writer(self):
        writer_task = self._writer_task
        queue = self._event_queue
        if writer_task is None:
            return
        if not writer_task.done():
            await queue.put(_WRITER_STOP)
            await writer_task
        else:
            await asyncio.gather(writer_task, return_exceptions=True)
        self._writer_task = None
        if self._storage_error is None:
            self._undrained_event_count = 0

    def _mark_storage_error(self):
        self._storage_error = "storage_error"
        self._state = "collector_error"
        self._accepting_events = False
        self._stop_requested = True
        self._stop_event.set()
        if self._storage_failure_event is not None:
            self._storage_failure_event.set()

    async def _heartbeat_loop(self):
        while not self._stop_requested:
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._heartbeat_interval,
                )
            except asyncio.TimeoutError:
                try:
                    await asyncio.to_thread(self._publish_status)
                except _CollectorStorageError:
                    self._mark_storage_error()
                    return

    async def _stop_heartbeat(self):
        task = self._heartbeat_task
        if task is None:
            return
        if task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._heartbeat_task = None

    def _publish_status(self):
        if self._session_id is None or not hasattr(
            self._store,
            "write_collector_status",
        ):
            return
        heartbeat_at = self._now()
        try:
            self._store.write_collector_status(
                session_id=self._session_id,
                provider=self._provider.capabilities().provider,
                coverage=self._provider.capabilities().coverage,
                state=self._state,
                confirmed_symbols=self._active.symbols,
                last_event_received_at=self._last_event_received_at,
                disconnect_count=self._disconnect_count,
                error=self._current_error(),
                heartbeat_at=heartbeat_at,
                queue_depth=(
                    0
                    if self._event_queue is None
                    else self._event_queue.qsize()
                ),
                queue_high_water=self._queue_high_water,
                dropped_event_count=self._dropped_event_count,
                undrained_event_count=self._undrained_event_count,
            )
            self._heartbeat_at = heartbeat_at
        except Exception as error:
            raise _CollectorStorageError from error

    def _publish_status_safely(self):
        try:
            self._publish_status()
        except _CollectorStorageError:
            self._mark_storage_error()

    def _current_error(self):
        return (
            self._storage_error
            or self._cleanup_error
            or self._stream_error
            or self._update_error
        )

    async def run(self):
        cleanup_completion = self._cleanup_completion
        if cleanup_completion is not None:
            if not cleanup_completion.done():
                raise RuntimeError("collector_stopping")
            if cleanup_completion.result() is not None:
                raise RuntimeError("collector_cleanup_failed")

        current_task = asyncio.current_task()
        if self._run_owner is not None and not self._run_owner.done():
            raise RuntimeError("collector_already_running")
        self._run_owner = current_task
        self._stop_requested = False
        self._stop_event = asyncio.Event()
        self._update_error = None
        self._storage_error = None
        self._cleanup_owner = None
        self._cleanup_completion = None
        self._session_id = uuid.uuid4().hex
        self._event_queue = asyncio.Queue(maxsize=self._queue_size)
        self._writer_task = None
        self._storage_failure_event = asyncio.Event()
        self._heartbeat_task = None
        self._queue_high_water = 0
        self._dropped_event_count = 0
        self._undrained_event_count = 0
        self._last_event_received_at = None
        self._heartbeat_at = None
        try:
            await self._run_owned()
        finally:
            if self._run_owner is current_task:
                self._run_owner = None

    async def _run_owned(self):
        capabilities = self._provider.capabilities()
        try:
            self._store.initialize()
            now = self._now()
            self._store.record_capabilities(capabilities, now)
            if hasattr(self._store, "begin_collector_session"):
                self._store.begin_collector_session(
                    session_id=self._session_id,
                    provider=capabilities.provider,
                    coverage=capabilities.coverage,
                    started_at=now,
                    stale_after_seconds=self._stale_after_seconds,
                )
        except Exception:
            self._mark_storage_error()
            return
        if capabilities.unavailable_reason is not None:
            self._state = "unavailable"
            self._stream_error = capabilities.unavailable_reason
            self._publish_status()
            return

        self._provider_closed = False
        self._writer_task = asyncio.create_task(self._writer_loop())
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop()
        )
        retry_index = 0
        try:
            while not self._stop_requested:
                self._state = "connecting"
                self._stream_error = None
                self._update_error = None
                self._accepting_events = False
                self._publish_status()
                request = self._desired
                stream_task = asyncio.create_task(
                    self._provider.stream_events(
                        request,
                        self._emit,
                        self._initial_confirmation,
                    )
                )
                storage_wait = asyncio.create_task(
                    self._storage_failure_event.wait()
                )
                done, _pending = await asyncio.wait(
                    (stream_task, storage_wait),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if storage_wait in done and self._storage_error is not None:
                    if not stream_task.done():
                        await self._provider.close()
                    if not stream_task.done():
                        stream_task.cancel()
                    await asyncio.gather(
                        stream_task,
                        return_exceptions=True,
                    )
                    break
                storage_wait.cancel()
                await asyncio.gather(
                    storage_wait,
                    return_exceptions=True,
                )
                try:
                    await stream_task
                    if not self._stop_requested:
                        raise RuntimeError("provider_stream_ended")
                except asyncio.CancelledError:
                    raise
                except _CollectorStorageError:
                    self._mark_storage_error()
                    break
                except Exception:
                    self._disconnect_count += 1
                    self._stream_error = "provider_error"
                    self._state = "retrying"
                    self._accepting_events = False
                    await self._cancel_update_worker()
                    self._close_active()
                    self._publish_status()
                    delay = self._retry_delays[
                        min(retry_index, len(self._retry_delays) - 1)
                    ]
                    retry_index += 1
                    if delay <= 0:
                        await asyncio.sleep(0)
                    else:
                        try:
                            await asyncio.wait_for(
                                self._stop_event.wait(),
                                delay,
                            )
                        except asyncio.TimeoutError:
                            pass
        finally:
            await self._cleanup_from_run()

    async def stop(self):
        self._stop_requested = True
        self._accepting_events = False
        self._stop_event.set()
        completion = self._cleanup_completion
        if completion is None or (
            completion.done() and completion.result() is not None
        ):
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
        self._raise_cleanup_result(completion.result())
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
            "error": self._current_error(),
            "heartbeat_at": (
                None
                if self._heartbeat_at is None
                else self._heartbeat_at.isoformat()
            ),
            "session_id": self._session_id,
            "queue_depth": (
                0
                if self._event_queue is None
                else self._event_queue.qsize()
            ),
            "queue_high_water": self._queue_high_water,
            "dropped_event_count": self._dropped_event_count,
            "undrained_event_count": self._undrained_event_count,
        }
