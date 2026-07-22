"""Point-in-time inputs shared by dashboard factor calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class AnalysisContext:
    """All data a factor may use for one ticker at one observation date.

    Histories are supplied by the caller already truncated at ``observation_date``
    so factor implementations never need to fetch data or look ahead.
    """

    ticker: str
    observation_date: pd.Timestamp
    history: pd.DataFrame
    benchmark_history: pd.DataFrame | None
    cache: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def history_asof(self) -> pd.DataFrame:
        """Return stock history sorted and truncated at the observation date."""
        key = "analysis:history_asof"
        if key not in self.cache:
            self.cache[key] = self.history.loc[
                self.history.index <= self.observation_date
            ].sort_index()
        return self.cache[key]

    def benchmark_asof(self) -> pd.DataFrame | None:
        """Return benchmark history through the same point in time, when present."""
        key = "analysis:benchmark_asof"
        if key not in self.cache:
            benchmark = self.benchmark_history
            self.cache[key] = (
                None
                if benchmark is None
                else benchmark.loc[benchmark.index <= self.observation_date].sort_index()
            )
        return self.cache[key]

    def cached(self, key: str, factory):
        """Compute a shared analysis value once for this context."""
        if key not in self.cache:
            self.cache[key] = factory()
        return self.cache[key]
