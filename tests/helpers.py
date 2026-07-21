from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(
    closes,
    highs=None,
    lows=None,
    opens=None,
    volumes=None,
    start="2020-01-01",
) -> pd.DataFrame:
    """Build a deterministic business-day OHLCV frame for tests."""
    close = np.asarray(closes, dtype=float)
    size = len(close)
    index = pd.bdate_range(start=start, periods=size)
    open_ = np.asarray(opens if opens is not None else close, dtype=float)
    high = np.asarray(highs if highs is not None else np.maximum(open_, close) * 1.01, dtype=float)
    low = np.asarray(lows if lows is not None else np.minimum(open_, close) * 0.99, dtype=float)
    volume = np.asarray(volumes if volumes is not None else np.full(size, 1_000_000), dtype=float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )

