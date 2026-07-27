"""Deterministic sampling and evidence audit for delisted identities."""

from __future__ import annotations

import hashlib


SAMPLE_VERSION = "delisted_identity_coverage_sample_v1"
DEFAULT_QUOTAS = {
    "strong_isin": 100,
    "ticker_only": 100,
    "conflicting_isin": 75,
}


def select_coverage_sample(catalog, history, quotas=None):
    """Select stable identity panels without hand-picking securities."""
    quotas = dict(quotas or DEFAULT_QUOTAS)
    if any(
        isinstance(quota, bool)
        or not isinstance(quota, int)
        or quota <= 0
        for quota in quotas.values()
    ):
        raise ValueError("quota counts must be positive integers")
    panels = {key: [] for key in quotas}
    seen = set()
    for raw in catalog:
        if not raw.get("backfill_eligible"):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        if ticker in seen:
            raise ValueError(f"duplicate eligible ticker: {ticker}")
        seen.add(ticker)
        panel = str(raw.get("identity_status") or "")
        if panel not in panels:
            continue
        audit = history.get(ticker) or {}
        digest = hashlib.sha256(
            f"{SAMPLE_VERSION}|{panel}|{ticker}".encode("utf-8")
        ).hexdigest()
        panels[panel].append(
            {
                "ticker": ticker,
                "exchange": str(raw.get("exchange") or "").strip().upper(),
                "name": str(raw.get("name") or ticker).strip(),
                "provider_isin": raw.get("provider_isin"),
                "identity_panel": panel,
                "valid_rows": int(audit.get("valid_rows") or 0),
                "last_date": audit.get("last_date"),
                "selection_hash": digest,
                "sample_version": SAMPLE_VERSION,
            }
        )

    selected = []
    for panel, quota in quotas.items():
        rows = sorted(
            panels[panel],
            key=lambda row: (row["selection_hash"], row["ticker"]),
        )
        if len(rows) < int(quota):
            raise ValueError(
                f"{panel} has {len(rows)} rows; {quota} required"
            )
        selected.extend(rows[:int(quota)])
    return tuple(sorted(selected, key=lambda row: row["ticker"]))
