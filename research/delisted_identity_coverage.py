"""Deterministic sampling and evidence audit for delisted identities."""

from __future__ import annotations

import hashlib
import json


SAMPLE_VERSION = "delisted_identity_coverage_sample_v1"
DEFAULT_QUOTAS = {
    "strong_isin": 100,
    "ticker_only": 100,
    "conflicting_isin": 75,
}
ADJUDICATION_VERSION = "delisted_identity_adjudication_v1"


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


def adjudicate_identity(sample_row, sec_candidates, provider_evidence):
    """Resolve evidence conservatively without treating ticker as identity."""
    ticker = str(sample_row.get("ticker") or "").strip().upper()
    candidates = tuple(sorted(sec_candidates, key=_canonical_json))
    provider_rows = tuple(sorted(provider_evidence, key=_canonical_json))
    type_conflicts = [
        row
        for row in provider_rows
        if str(row.get("key_type") or "") == "security_type"
        and " ".join(
            str(row.get("value") or "").lower().split()
        )
        not in {"common stock", "common equity"}
    ]
    if type_conflicts:
        return {
            "ticker": ticker,
            "cik": None,
            "link_status": "rejected",
            "decision_rule": "security_type_contradiction",
            "rule_version": ADJUDICATION_VERSION,
            "reason_codes": ["security_type_contradiction"],
            "supporting_evidence": [],
            "conflicting_evidence": type_conflicts,
        }
    candidate_by_cik = {
        str(candidate.get("cik") or ""): candidate
        for candidate in candidates
    }
    sample_isin = str(
        sample_row.get("provider_isin") or ""
    ).strip().upper()
    provider_ciks = {
        str(row.get("cik") or "")
        for row in provider_rows
        if str(row.get("key_type") or "") == "isin_cik"
        and str(row.get("isin") or "").strip().upper() == sample_isin
    }
    if len(provider_ciks) > 1:
        conflicts = [
            row
            for row in provider_rows
            if str(row.get("cik") or "") in provider_ciks
        ]
        return {
            "ticker": ticker,
            "cik": None,
            "link_status": "review_required",
            "decision_rule": "conflicting_isin_cik",
            "rule_version": ADJUDICATION_VERSION,
            "reason_codes": ["conflicting_isin_cik"],
            "supporting_evidence": [],
            "conflicting_evidence": conflicts,
        }
    if len(provider_ciks) == 1:
        cik = next(iter(provider_ciks))
        candidate = candidate_by_cik.get(cik)
        if candidate and any(
            reason in {"exact_current_name", "exact_former_name"}
            for reason in candidate.get("match_reasons") or ()
        ):
            return {
                "ticker": ticker,
                "cik": cik,
                "link_status": "confirmed",
                "decision_rule": "isin_cik_plus_exact_name",
                "rule_version": ADJUDICATION_VERSION,
                "reason_codes": [],
                "supporting_evidence": [candidate, *provider_rows],
                "conflicting_evidence": [],
            }
    if len(candidates) == 1:
        candidate = candidates[0]
        former_name = candidate.get("matched_former_name")
        first_date = sample_row.get("first_date")
        last_date = sample_row.get("last_date")
        if (
            "exact_former_name" in candidate.get("match_reasons", ())
            and former_name
            and first_date
            and last_date
            and former_name["from"] <= last_date
            and first_date <= former_name["to"]
        ):
            return {
                "ticker": ticker,
                "cik": str(candidate["cik"]),
                "link_status": "confirmed",
                "decision_rule": "dated_former_name_overlap",
                "rule_version": ADJUDICATION_VERSION,
                "reason_codes": [],
                "supporting_evidence": [candidate],
                "conflicting_evidence": [],
            }
    if len(candidates) > 1:
        return {
            "ticker": ticker,
            "cik": None,
            "link_status": "review_required",
            "decision_rule": "competing_ciks",
            "rule_version": ADJUDICATION_VERSION,
            "reason_codes": ["competing_ciks"],
            "supporting_evidence": [],
            "conflicting_evidence": list(candidates),
        }
    if (
        candidates
        and all(
            candidate.get("match_reasons") == ["current_ticker"]
            for candidate in candidates
        )
    ):
        return {
            "ticker": ticker,
            "cik": None,
            "link_status": "review_required",
            "decision_rule": "ticker_only_match",
            "rule_version": ADJUDICATION_VERSION,
            "reason_codes": ["ticker_only_match"],
            "supporting_evidence": list(candidates),
            "conflicting_evidence": [],
        }
    if len(candidates) == 1 and any(
        reason in {"exact_current_name", "exact_former_name"}
        for reason in candidates[0].get("match_reasons", ())
    ):
        return {
            "ticker": ticker,
            "cik": None,
            "link_status": "review_required",
            "decision_rule": "undated_exact_name",
            "rule_version": ADJUDICATION_VERSION,
            "reason_codes": ["undated_exact_name"],
            "supporting_evidence": [candidates[0]],
            "conflicting_evidence": [],
        }
    if not candidates and not provider_rows:
        return {
            "ticker": ticker,
            "cik": None,
            "link_status": "unresolved",
            "decision_rule": "no_identity_candidates",
            "rule_version": ADJUDICATION_VERSION,
            "reason_codes": ["no_identity_candidates"],
            "supporting_evidence": [],
            "conflicting_evidence": [],
        }
    raise NotImplementedError("identity evidence branch is not implemented")


def normalize_provider_evidence(payload, observed_at):
    """Normalize provider identity hints without treating them as history."""
    rows = []
    for raw in payload:
        isin = str(raw.get("isin") or "").strip().upper()
        cik = str(raw.get("cik") or "").strip()
        if not isin or not cik.isdigit():
            continue
        rows.append(
            {
                "key_type": "isin_cik",
                "isin": isin,
                "cik": cik.zfill(10),
                "ticker": str(raw.get("code") or "").strip().upper(),
                "name": str(raw.get("name") or "").strip(),
                "available_at": str(observed_at),
                "source": "eodhd",
            }
        )
    return tuple(
        sorted(rows, key=lambda row: (row["isin"], row["cik"]))
    )


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
