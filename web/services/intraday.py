"""Read-only status access for the intraday market-data collector."""


class IntradayStatusService:
    """Expose collector state without starting or changing collection."""

    def __init__(
        self,
        collector=None,
        store=None,
        stale_after_seconds=30,
    ):
        self._collector = collector
        self._store = store
        self._stale_after_seconds = stale_after_seconds

    def snapshot(self):
        if self._collector is not None:
            return self._collector.snapshot()
        if self._store is not None:
            return self._store.read_collector_status(
                stale_after_seconds=self._stale_after_seconds,
            )
        return {
            "state": "unavailable",
            "provider": None,
            "coverage": None,
            "subscribed_symbols": [],
            "last_event_received_at": None,
            "disconnect_count": 0,
            "error": "collector_not_configured",
        }
