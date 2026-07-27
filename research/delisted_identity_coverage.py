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
LINK_STATUSES = (
    "confirmed",
    "rejected",
    "review_required",
    "unresolved",
)


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
    if (
        str(sample_row.get("identity_panel") or "")
        == "conflicting_isin"
        and provider_ciks
    ):
        return {
            "ticker": ticker,
            "cik": None,
            "link_status": "review_required",
            "decision_rule": "catalog_conflicting_isin",
            "rule_version": ADJUDICATION_VERSION,
            "reason_codes": ["catalog_conflicting_isin"],
            "supporting_evidence": [],
            "conflicting_evidence": list(provider_rows),
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


def summarize_coverage(sample, decisions, sic_audits, usage):
    """Aggregate fixed-sample coverage without mixing identity panels."""
    sample_rows = tuple(sample)
    decision_rows = tuple(decisions)
    sample_by_ticker = _unique_rows(sample_rows, "sample")
    decisions_by_ticker = _unique_rows(decision_rows, "decision")
    if set(sample_by_ticker) != set(decisions_by_ticker):
        raise ValueError("sample and decision ticker sets must match")

    panel_counts = {}
    total_counts = _empty_status_counts()
    source_counts = {"provider_assisted": 0, "sec_only": 0}
    reason_counts = {}
    reason_examples = {}
    for ticker in sorted(sample_by_ticker):
        sample_row = sample_by_ticker[ticker]
        decision = decisions_by_ticker[ticker]
        panel = str(sample_row.get("identity_panel") or "unknown")
        status = str(decision.get("link_status") or "")
        if status not in LINK_STATUSES:
            raise ValueError(f"unsupported link status: {status}")
        panel_summary = panel_counts.setdefault(
            panel,
            {
                "sample_count": 0,
                "decision_counts": _empty_status_counts(),
            },
        )
        panel_summary["sample_count"] += 1
        panel_summary["decision_counts"][status] += 1
        total_counts[status] += 1
        if status == "confirmed":
            rule = str(decision.get("decision_rule") or "")
            source = (
                "provider_assisted"
                if rule == "isin_cik_plus_exact_name"
                else "sec_only"
            )
            source_counts[source] += 1
        for raw_reason in decision.get("reason_codes") or ():
            reason = str(raw_reason)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            examples = reason_examples.setdefault(reason, [])
            if len(examples) < 5:
                examples.append(ticker)

    for panel_summary in panel_counts.values():
        panel_summary["confirmation_rate"] = _rate(
            panel_summary["decision_counts"]["confirmed"],
            panel_summary["sample_count"],
        )

    sic_audits = dict(sic_audits or {})
    observations = tuple(sic_audits.get("observations") or ())
    statuses = tuple(sic_audits.get("statuses") or ())
    available_dates = sorted(
        str(row.get("available_at"))
        for row in observations
        if row.get("available_at")
    )
    sic_status_counts = {}
    for row in statuses:
        status = str(row.get("status") or "unknown")
        sic_status_counts[status] = sic_status_counts.get(status, 0) + 1
    sic_summary = {
        "available_ciks": len(
            {
                str(row.get("cik"))
                for row in observations
                if row.get("cik")
            }
        ),
        "observation_count": len(observations),
        "status_counts": dict(sorted(sic_status_counts.items())),
        "earliest_available_at": (
            available_dates[0] if available_dates else None
        ),
        "latest_available_at": (
            available_dates[-1] if available_dates else None
        ),
    }

    normalized_usage = dict(usage or {})
    projections = normalized_usage.pop("projection_panels", {}) or {}
    normalized_usage["projection_panels"] = {
        str(panel): {
            "source_sample_count": int(
                values.get("sample_count") or 0
            ),
            "population_count": int(
                values.get("population_count") or 0
            ),
            "projected_storage_bytes": int(
                values.get("projected_storage_bytes") or 0
            ),
            "projected_runtime_seconds": float(
                values.get("projected_runtime_seconds") or 0
            ),
        }
        for panel, values in sorted(projections.items())
    }

    return {
        "sample_count": len(sample_rows),
        "decision_counts": total_counts,
        "confirmation_rate": _rate(
            total_counts["confirmed"],
            len(sample_rows),
        ),
        "identity_panels": dict(sorted(panel_counts.items())),
        "confirmation_sources": source_counts,
        "reason_counts": dict(sorted(reason_counts.items())),
        "reason_examples": {
            reason: tuple(examples)
            for reason, examples in sorted(reason_examples.items())
        },
        "sic": sic_summary,
        "usage": normalized_usage,
    }


def _unique_rows(rows, label):
    indexed = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            raise ValueError(f"{label} ticker is required")
        if ticker in indexed:
            raise ValueError(f"duplicate {label} ticker: {ticker}")
        indexed[ticker] = row
    return indexed


def _empty_status_counts():
    return {status: 0 for status in LINK_STATUSES}


def _rate(numerator, denominator):
    if denominator == 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "value": None,
            "reason": "no_eligible_rows",
        }
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator,
        "reason": None,
    }


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
