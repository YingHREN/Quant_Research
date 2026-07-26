"""Build a deterministic, provenance-rich research universe catalog."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from data.sector_classification import classify_sic


SCHEMA_VERSION = "universe_catalog_v1"
MIN_HISTORY_ROWS = 60


def build_catalog(universe_payload, identities, history_lengths, *, asof):
    if not isinstance(universe_payload, dict):
        raise TypeError("universe_payload must be a mapping")
    if str(universe_payload.get("asof")) != str(asof):
        raise ValueError("catalog asof must match universe observation date")
    securities = universe_payload.get("securities")
    if not isinstance(securities, list):
        raise ValueError("universe securities must be a list")

    seen = set()
    eligible = []
    excluded = []
    for raw_security in securities:
        ticker = str(raw_security.get("ticker") or "").strip().upper()
        if ticker in seen:
            raise ValueError(f"duplicate ticker: {ticker}")
        seen.add(ticker)
        identity = identities.get(ticker)
        if not isinstance(identity, dict):
            raise ValueError(f"missing SEC identity: {ticker}")
        history_rows = int(history_lengths.get(ticker, 0))
        if history_rows < MIN_HISTORY_ROWS:
            excluded.append(
                {
                    "ticker": ticker,
                    "reason": "history_below_60_rows",
                    "history_rows": history_rows,
                }
            )
            continue
        classification = classify_sic(
            identity.get("sic"),
            identity.get("sicDescription", ""),
        )
        row = dict(raw_security)
        row.update(
            {
                "ticker": ticker,
                "cik": int(identity["cik"]),
                "history_rows": history_rows,
                "classification": classification.to_dict(),
            }
        )
        eligible.append(row)

    eligible.sort(key=lambda row: row["ticker"])
    excluded.sort(key=lambda row: row["ticker"])
    sector_counts = Counter(
        row["classification"]["sector_key"] for row in eligible
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "asof": str(asof),
        "universe_key": str(universe_payload.get("selection_rule") or ""),
        "thresholds": dict(universe_payload.get("thresholds") or {}),
        "candidate_count": len(securities),
        "eligible_count": len(eligible),
        "excluded": excluded,
        "sector_counts": dict(sorted(sector_counts.items())),
        "securities": eligible,
    }


def write_catalog(source_root, identity_root, output_path):
    source_root = Path(source_root)
    identity_root = Path(identity_root)
    output_path = Path(output_path)
    universe_payload = json.loads(
        (source_root / "expanded_universe_liquid100m_v1.json").read_text()
    )
    identities = {}
    history_lengths = {}
    for security in universe_payload.get("securities", ()):
        ticker = str(security.get("ticker") or "").strip().upper()
        identity_path = identity_root / f"{ticker}.json"
        if identity_path.exists():
            identities[ticker] = json.loads(identity_path.read_text())
        history_path = source_root / f"{ticker}.json"
        if history_path.exists():
            history = json.loads(history_path.read_text())
            if isinstance(history, list):
                history_lengths[ticker] = len(history)
    catalog = build_catalog(
        universe_payload,
        identities,
        history_lengths,
        asof=universe_payload.get("asof"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return catalog
