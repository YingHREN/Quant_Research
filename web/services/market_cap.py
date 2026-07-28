"""Point-in-time company market-cap API fields."""

from __future__ import annotations

import math

from web.contracts import iso_date


def market_cap_fields(value, asof):
    """Normalize a company market cap and assign its stable display tier."""
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = None
    if normalized is None or not math.isfinite(normalized) or normalized <= 0:
        return {
            "market_cap": None,
            "market_cap_asof": None,
            "market_cap_tier": "unavailable",
        }
    tier = (
        "mega"
        if normalized >= 200_000_000_000
        else "large"
        if normalized >= 10_000_000_000
        else "mid"
        if normalized >= 2_000_000_000
        else "small"
        if normalized >= 300_000_000
        else "micro"
    )
    return {
        "market_cap": normalized,
        "market_cap_asof": iso_date(asof),
        "market_cap_tier": tier,
    }
