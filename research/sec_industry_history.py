"""Parse and derive point-in-time SEC SIC classifications."""

from __future__ import annotations

from datetime import datetime
from datetime import date
import re


INTERVAL_RULE_VERSION = "sec_sic_interval_v1"
TAXONOMY_VERSION = "sec_sic_v1"
PARSER_VERSION = "sec_submission_header_sic_v1"
_ACCESSION_RE = re.compile(r"^(\d{10})-\d{2}-\d{6}$")
_SIC_RE = re.compile(
    r"STANDARD\s+INDUSTRIAL\s+CLASSIFICATION\s*:\s*"
    r"([^\[\r\n]+?)\s*\[(\d{4})\]",
    re.IGNORECASE,
)


def parse_sec_submission_header(
    text,
    accession,
    filing_date,
    accepted_at,
):
    """Parse an explicit SIC observation from an EDGAR submission header."""
    accession_match = _ACCESSION_RE.fullmatch(str(accession or ""))
    if not accession_match:
        raise ValueError("invalid accession number")
    filed = date.fromisoformat(str(filing_date or "")).isoformat()
    available_at = _canonical_timestamp(accepted_at)
    matches = {
        (" ".join(label.split()).upper(), sic)
        for label, sic in _SIC_RE.findall(str(text or ""))
    }
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("conflicting SIC lines in submission header")
    industry_label, sic = next(iter(matches))
    return {
        "cik": accession_match.group(1),
        "sic": sic,
        "industry_label": industry_label,
        "accession_number": str(accession),
        "filing_date": filed,
        "accepted_at": available_at,
        "available_at": available_at,
        "source": "sec_edgar",
        "parser_version": PARSER_VERSION,
    }


def build_sic_intervals(observations):
    """Build half-open SIC intervals from filing availability times."""
    unique = []
    by_accession = {}
    by_time = {}
    for raw in observations:
        cik = str(raw["cik"])
        sic = str(raw["sic"])
        accession = str(raw["accession_number"])
        available_at = _canonical_timestamp(raw["available_at"])
        prior_accession = by_accession.get(accession)
        if prior_accession is not None:
            if prior_accession != (cik, sic):
                raise ValueError(
                    "duplicate accession has conflicting SIC"
                )
            continue
        time_key = (cik, available_at)
        prior_sic = by_time.get(time_key)
        if prior_sic is not None and prior_sic != sic:
            raise ValueError(
                "conflicting SIC observations at the same available time"
            )
        by_accession[accession] = (cik, sic)
        by_time[time_key] = sic
        unique.append(
            {
                **raw,
                "cik": cik,
                "sic": sic,
                "available_at": available_at,
                "accession_number": accession,
            }
        )
    ordered = sorted(
        unique,
        key=lambda row: (
            str(row["cik"]),
            _timestamp(row["available_at"]),
            str(row["accession_number"]),
        ),
    )
    intervals = []
    for observation in ordered:
        cik = str(observation["cik"])
        sic = str(observation["sic"])
        available_at = _canonical_timestamp(observation["available_at"])
        accession = str(observation["accession_number"])
        previous = intervals[-1] if intervals else None
        if previous and previous["cik"] == cik and previous["sic"] == sic:
            previous["last_supporting_accession"] = accession
            previous["observation_count"] += 1
            continue
        if previous and previous["cik"] == cik:
            previous["valid_to"] = available_at
        intervals.append(
            {
                "cik": cik,
                "sic": sic,
                "valid_from": available_at,
                "valid_to": None,
                "first_accession": accession,
                "last_supporting_accession": accession,
                "observation_count": 1,
                "taxonomy_version": TAXONOMY_VERSION,
                "interval_rule_version": INTERVAL_RULE_VERSION,
            }
        )
    return tuple(intervals)


def classification_asof(intervals, asof):
    """Return the interval visible at an aware point in time."""
    target = _timestamp(asof)
    for interval in intervals:
        start = _timestamp(interval["valid_from"])
        finish = (
            None
            if interval.get("valid_to") is None
            else _timestamp(interval["valid_to"])
        )
        if start <= target and (finish is None or target < finish):
            return interval
    return None


def _timestamp(value):
    text = str(value)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def _canonical_timestamp(value):
    parsed = _timestamp(value)
    text = parsed.isoformat()
    return text.replace("+00:00", "Z")
