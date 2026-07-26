"""Bounded selected-ticker cache for causal historical entry signals."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from hashlib import blake2b
from threading import RLock

import pandas as pd

from research.entry_signals import (
    ENTRY_SIGNAL_VERSION,
    build_entry_signal_rows,
)


_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class EntrySignalService:
    def __init__(self, max_cache_size=16):
        if isinstance(max_cache_size, bool) or not isinstance(
            max_cache_size,
            int,
        ):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._lock = RLock()

    def build(self, ticker, history):
        normalized_ticker = str(ticker).strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker must be non-empty")
        fingerprint = _history_fingerprint(history)
        key = (
            normalized_ticker,
            ENTRY_SIGNAL_VERSION,
            fingerprint,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)

        rows = build_entry_signal_rows(history)
        with self._lock:
            self._cache[key] = deepcopy(rows)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        return deepcopy(rows)


def merge_entry_signal_rows(chart, signals):
    """Merge entry evidence by exact ISO trading date."""
    by_date = {}
    for signal in signals:
        date = signal.get("time") if isinstance(signal, dict) else None
        if not isinstance(date, str) or date in by_date:
            raise ValueError("entry signal dates must be unique ISO strings")
        by_date[date] = signal

    merged = []
    for row in chart:
        date = row.get("time") if isinstance(row, dict) else None
        signal = by_date.get(date)
        if signal is None:
            raise ValueError("entry signal history is missing a chart date")
        merged.append({**row, **signal, "time": date})
    return merged


def _history_fingerprint(history):
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a pandas DataFrame")
    missing = [column for column in _OHLCV_COLUMNS if column not in history]
    if missing:
        raise ValueError("history must contain OHLCV columns")
    frame = history.loc[:, _OHLCV_COLUMNS].sort_index()
    hashed = pd.util.hash_pandas_object(frame, index=True)
    digest = blake2b(digest_size=20)
    digest.update(hashed.to_numpy(dtype="uint64").tobytes())
    return digest.hexdigest()
