"""Read-only orchestration for point-in-time policy context."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock

import pandas as pd

from research.policy_context import (
    POLICY_SERIES_IDS,
    build_policy_context,
    unavailable_policy_context,
)
from web.services.macro_store import (
    MacroDataUnavailable,
    MacroObservationStore,
)


class PolicyContextService:
    def __init__(self, database_path, max_cache_size=256):
        self._database_path = Path(database_path)
        self._store = MacroObservationStore(self._database_path)
        self._cache = {}
        self._max_cache_size = max_cache_size
        self._lock = RLock()

    def build(self, asof=None):
        cutoff = _asof_cutoff(asof)
        key = (self.cache_token(), cutoff.isoformat())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return deepcopy(cached)
        try:
            rows = self._store.load_available(
                cutoff,
                series_ids=POLICY_SERIES_IDS,
            )
            payload = build_policy_context(rows, cutoff)
        except (MacroDataUnavailable, ValueError, TypeError):
            payload = unavailable_policy_context(
                "policy_data_unavailable",
                cutoff,
            )
        with self._lock:
            self._cache[key] = deepcopy(payload)
            while len(self._cache) > self._max_cache_size:
                self._cache.pop(next(iter(self._cache)))
        return deepcopy(payload)

    def cache_token(self):
        try:
            stat = self._database_path.stat()
        except OSError:
            return ("missing",)
        return (stat.st_mtime_ns, stat.st_size)


def _asof_cutoff(asof):
    if asof is None:
        return pd.Timestamp.now(tz="UTC")
    value = pd.Timestamp(asof)
    if value.tz is None:
        value = value.normalize() + pd.Timedelta(days=1)
        value -= pd.Timedelta(microseconds=1)
        return value.tz_localize("UTC")
    return value.tz_convert("UTC")
