"""Read-only status access for the intraday market-data collector."""


class IntradayStatusService:
    """Expose collector state without starting or changing collection."""

    def __init__(self, collector=None):
        self._collector = collector

    def snapshot(self):
        if self._collector is None:
            return {
                "state": "unavailable",
                "provider": None,
                "coverage": None,
                "subscribed_symbols": [],
                "last_event_received_at": None,
                "disconnect_count": 0,
                "error": "collector_not_configured",
            }
        return self._collector.snapshot()
