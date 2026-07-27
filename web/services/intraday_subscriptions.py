from datetime import datetime, timezone

from marketdata.base import SubscriptionRequest


FIXED_SYMBOLS = ("SPY", "QQQ", "SOXX")
MAX_TOTAL_SYMBOLS = 30
MAX_USER_SYMBOLS = MAX_TOTAL_SYMBOLS - len(FIXED_SYMBOLS)


class SubscriptionLimitExceeded(ValueError):
    pass


class IntradaySubscriptionService:
    def __init__(self, store, status_service=None):
        self._store = store
        self._status_service = status_service

    def replace(self, symbols):
        if not isinstance(symbols, (list, tuple)):
            raise ValueError("symbols must be an array")
        normalized = SubscriptionRequest(
            symbols,
            max_symbols=MAX_TOTAL_SYMBOLS,
        ).symbols
        fixed = set(FIXED_SYMBOLS)
        user_symbols = tuple(symbol for symbol in normalized if symbol not in fixed)
        if len(user_symbols) > MAX_USER_SYMBOLS:
            raise SubscriptionLimitExceeded(
                f"at most {MAX_USER_SYMBOLS} user symbols are supported"
            )
        self._store.replace_subscription_request(
            user_symbols,
            datetime.now(timezone.utc),
        )
        return self.snapshot()

    def snapshot(self):
        request = self._store.read_subscription_request()
        user_symbols = [
            symbol
            for symbol in request["user_symbols"]
            if symbol not in FIXED_SYMBOLS
        ]
        requested = list(FIXED_SYMBOLS) + user_symbols
        status = (
            self._status_service.snapshot()
            if self._status_service is not None
            else {
                "state": "unavailable",
                "provider": None,
                "coverage": None,
                "subscribed_symbols": [],
                "last_event_received_at": None,
                "error": "collector_not_configured",
            }
        )
        confirmed = list(status.get("subscribed_symbols") or [])
        confirmed_set = set(confirmed)
        return {
            "revision": request["revision"],
            "updated_at": request["updated_at"],
            "fixed_symbols": list(FIXED_SYMBOLS),
            "user_symbols": user_symbols,
            "requested_symbols": requested,
            "confirmed_symbols": confirmed,
            "pending_symbols": [
                symbol for symbol in requested if symbol not in confirmed_set
            ],
            "capacity": {
                "used": len(requested),
                "limit": MAX_TOTAL_SYMBOLS,
                "remaining": MAX_TOTAL_SYMBOLS - len(requested),
            },
            "collector": status,
        }
