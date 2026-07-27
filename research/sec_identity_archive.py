"""Strict local index for SEC submissions identity metadata."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date
import json
from pathlib import Path
import re
from zipfile import ZipFile


_CIK_FILE_RE = re.compile(r"^CIK(\d{10})\.json$")
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:INCORPORATED|INC|CORPORATION|CORP|LIMITED|LTD|PLC|LLC)\b$"
)
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")


def normalize_legal_name(value):
    """Normalize exact legal-name evidence without fuzzy matching."""
    text = _NON_ALNUM_RE.sub(" ", str(value or "").upper()).strip()
    while True:
        shortened = _LEGAL_SUFFIX_RE.sub("", text).strip()
        if shortened == text:
            return text
        text = shortened


def iter_submission_records(path):
    """Yield validated, minimal SEC identity records from a bulk ZIP."""
    path = Path(path)
    with ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            match = _CIK_FILE_RE.fullmatch(name)
            if not match:
                continue
            payload = json.loads(archive.read(name).decode("utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("SEC submission payload must be an object")
            cik = str(payload.get("cik") or "").strip().zfill(10)
            if cik != match.group(1):
                raise ValueError(f"CIK mismatch for {name}")
            former_names = []
            for raw in payload.get("formerNames") or ():
                former_names.append(
                    {
                        "name": str(raw.get("name") or "").strip(),
                        "normalized_name": normalize_legal_name(
                            raw.get("name")
                        ),
                        "from": _iso_date(raw.get("from")),
                        "to": _iso_date(raw.get("to")),
                    }
                )
            filings = payload.get("filings") or {}
            yield {
                "cik": cik,
                "name": str(payload.get("name") or "").strip(),
                "normalized_name": normalize_legal_name(payload.get("name")),
                "tickers": tuple(
                    sorted(
                        {
                            str(value).strip().upper()
                            for value in payload.get("tickers") or ()
                            if str(value).strip()
                        }
                    )
                ),
                "exchanges": tuple(
                    str(value or "").strip().upper()
                    for value in payload.get("exchanges") or ()
                ),
                "sic": str(payload.get("sic") or "").strip() or None,
                "sic_description": str(
                    payload.get("sicDescription") or ""
                ).strip(),
                "former_names": tuple(former_names),
                "recent_filings": _recent_filings(
                    filings.get("recent") or {}
                ),
                "filing_files": tuple(filings.get("files") or ()),
            }


def build_identity_index(records, sample_rows=None):
    """Build deterministic exact-name and current-ticker lookup indexes."""
    target_names = None
    target_tickers = None
    if sample_rows is not None:
        sample_rows = tuple(sample_rows)
        target_names = {
            normalize_legal_name(row.get("name"))
            for row in sample_rows
            if normalize_legal_name(row.get("name"))
        }
        target_tickers = {
            str(row.get("ticker") or "").strip().upper()
            for row in sample_rows
            if str(row.get("ticker") or "").strip()
        }
    by_cik = {}
    by_name = defaultdict(list)
    by_ticker = defaultdict(list)
    seen_ciks = set()
    for record in records:
        cik = str(record["cik"])
        if cik in seen_ciks:
            raise ValueError(f"duplicate CIK record: {cik}")
        seen_ciks.add(cik)
        if target_names is not None:
            record_names = {
                record["normalized_name"],
                *(
                    row["normalized_name"]
                    for row in record["former_names"]
                ),
            }
            if not (
                record_names.intersection(target_names)
                or set(record["tickers"]).intersection(target_tickers)
            ):
                continue
        by_cik[cik] = record
        if record["normalized_name"]:
            by_name[record["normalized_name"]].append(
                (cik, "exact_current_name", None)
            )
        for former_name in record["former_names"]:
            if former_name["normalized_name"]:
                by_name[former_name["normalized_name"]].append(
                    (cik, "exact_former_name", former_name)
                )
        for ticker in record["tickers"]:
            by_ticker[ticker].append(cik)
    return {
        "by_cik": by_cik,
        "by_name": {
            key: tuple(sorted(value, key=lambda row: (row[0], row[1])))
            for key, value in by_name.items()
        },
        "by_ticker": {
            key: tuple(sorted(set(value)))
            for key, value in by_ticker.items()
        },
    }


def find_sec_candidates(sample_row, index):
    """Return candidate evidence without making an identity decision."""
    ticker = str(sample_row.get("ticker") or "").strip().upper()
    normalized_name = normalize_legal_name(sample_row.get("name"))
    matches = {}
    for cik, reason, former_name in index["by_name"].get(
        normalized_name,
        (),
    ):
        row = matches.setdefault(
            cik,
            {
                "cik": cik,
                "match_reasons": [],
                "matched_former_name": None,
                "sec_record": index["by_cik"][cik],
            },
        )
        row["match_reasons"].append(reason)
        if former_name is not None:
            row["matched_former_name"] = former_name
    for cik in index["by_ticker"].get(ticker, ()):
        row = matches.setdefault(
            cik,
            {
                "cik": cik,
                "match_reasons": [],
                "matched_former_name": None,
                "sec_record": index["by_cik"][cik],
            },
        )
        row["match_reasons"].append("current_ticker")
    for row in matches.values():
        row["match_reasons"] = sorted(set(row["match_reasons"]))
    return tuple(matches[cik] for cik in sorted(matches))


def _iso_date(value):
    return date.fromisoformat(str(value or "")).isoformat()


def _recent_filings(recent):
    accessions = recent.get("accessionNumber") or ()
    rows = []
    fields = (
        ("filing_date", "filingDate"),
        ("acceptance_datetime", "acceptanceDateTime"),
        ("form", "form"),
        ("primary_document", "primaryDocument"),
    )
    for index, accession in enumerate(accessions):
        row = {"accession_number": str(accession)}
        for output, source in fields:
            values = recent.get(source) or ()
            row[output] = (
                str(values[index])
                if index < len(values) and values[index] is not None
                else None
            )
        rows.append(row)
    return tuple(rows)
