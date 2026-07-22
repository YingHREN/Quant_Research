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
