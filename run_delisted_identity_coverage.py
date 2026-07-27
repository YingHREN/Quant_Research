"""Run a resumable delisted identity and industry coverage pilot."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from research.delisted_identity_coverage import (
    DEFAULT_QUOTAS,
    SAMPLE_VERSION,
    adjudicate_identity,
    normalize_provider_evidence,
    select_coverage_sample,
)
from research.delisted_reference_store import DelistedReferenceStore
from research.sec_identity_archive import (
    build_identity_index,
    find_sec_candidates,
    iter_submission_records,
)
from research.sec_industry_history import (
    build_sic_intervals,
    parse_sec_submission_header,
)


SEC_SUBMISSIONS_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/"
    "bulkdata/submissions.zip"
)
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


def collect_artifact(name, cache_root, fetcher=None):
    """Collect or reuse a content-verified immutable source artifact."""
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    if name != "sec_submissions":
        raise ValueError(f"unknown artifact: {name}")
    artifact_path = cache_root / "submissions.zip"
    manifest_path = cache_root / "manifest.json"
    cached = _verified_manifest(artifact_path, manifest_path)
    if cached is not None:
        return {**cached, "reused": True}
    if artifact_path.exists() or manifest_path.exists():
        raise ValueError("cached artifact failed content verification")

    headers = {"Accept": "application/zip"}
    if fetcher is None:
        user_agent = str(os.environ.get("SEC_USER_AGENT") or "").strip()
        if not user_agent:
            raise ValueError("SEC_USER_AGENT is required")
        headers["User-Agent"] = user_agent
        fetcher = _fetch_url
    payload = fetcher(
        SEC_SUBMISSIONS_URL,
        headers,
    )
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("artifact response must be non-empty bytes")
    _validate_sec_submissions_zip(payload)
    digest = hashlib.sha256(payload).hexdigest()
    _atomic_write(artifact_path, payload)
    manifest = {
        "artifact_name": name,
        "sha256": digest,
        "byte_count": len(payload),
        "status": "verified",
    }
    _atomic_write(
        manifest_path,
        (
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8"),
    )
    return {**manifest, "reused": False}


def collect_provider_sample(
    decisions,
    cache_root,
    *,
    token,
    fetcher=None,
    limit=300,
    offline=False,
):
    """Collect bounded provider evidence for unresolved identities."""
    cache_root = Path(cache_root) / "provider"
    cache_root.mkdir(parents=True, exist_ok=True)
    fetcher = fetcher or _fetch_eodhd_fundamentals
    eligible = []
    seen = set()
    for row in decisions:
        if str(row.get("link_status") or "") != "unresolved":
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not _TICKER_RE.fullmatch(ticker):
            raise ValueError(f"invalid provider ticker: {ticker}")
        if ticker in seen:
            raise ValueError(f"duplicate provider ticker: {ticker}")
        seen.add(ticker)
        eligible.append(ticker)
    eligible.sort()
    eligible = eligible[: int(limit)]

    statuses = []
    payloads = {}
    for ticker in eligible:
        path = cache_root / f"{ticker}.json"
        cached = _cached_json_object(path)
        if cached is not None:
            payloads[ticker] = cached
            statuses.append({"ticker": ticker, "status": "success", "reused": True})
            continue
        if offline:
            statuses.append({"ticker": ticker, "status": "offline_missing", "reused": False})
            continue
        try:
            payload = fetcher(ticker, token)
            if not isinstance(payload, dict) or not payload:
                raise ValueError("provider response must be a non-empty object")
            _atomic_write(
                path,
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            payloads[ticker] = payload
            statuses.append({"ticker": ticker, "status": "success", "reused": False})
        except Exception as exc:  # external provider boundary
            statuses.append(
                {
                    "ticker": ticker,
                    "status": _provider_error_status(exc),
                    "reused": False,
                }
            )
    status_counts = {}
    for row in statuses:
        status = row["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "requested": len(eligible),
        "statuses": tuple(statuses),
        "status_counts": dict(sorted(status_counts.items())),
        "payloads": payloads,
    }


def collect_sec_header_sample(
    decisions,
    identity_index,
    cache_root,
    *,
    fetcher=None,
    offline=False,
    cik_limit=50,
    filings_per_cik=2,
):
    """Collect a bounded sample of filing headers for confirmed CIKs."""
    root = Path(cache_root) / "sec" / "filings"
    root.mkdir(parents=True, exist_ok=True)
    headers = {"Accept": "text/plain"}
    if fetcher is None:
        user_agent = str(os.environ.get("SEC_USER_AGENT") or "").strip()
        if not user_agent and not offline:
            raise ValueError("SEC_USER_AGENT is required")
        headers["User-Agent"] = user_agent
        fetcher = _fetch_sec_submission
    confirmed = sorted(
        {
            str(row["cik"])
            for row in decisions
            if row.get("link_status") == "confirmed" and row.get("cik")
        }
    )[: int(cik_limit)]
    observations = []
    statuses = []
    for cik in confirmed:
        record = identity_index["by_cik"].get(cik) or {}
        filings = [
            row
            for row in record.get("recent_filings") or ()
            if str(row.get("form") or "").upper()
            in {"10-K", "10-Q", "20-F", "40-F"}
            and row.get("accession_number")
            and row.get("filing_date")
            and row.get("acceptance_datetime")
        ]
        filings.sort(
            key=lambda row: (
                str(row["filing_date"]),
                str(row["accession_number"]),
            )
        )
        filings = _edge_sample(filings, int(filings_per_cik))
        for filing in filings:
            accession = str(filing["accession_number"])
            path = root / f"{accession}.txt"
            reused = path.exists()
            if reused:
                raw = path.read_bytes()
                if not raw:
                    raise ValueError(
                        f"cached SEC filing is corrupt: {accession}"
                    )
            elif offline:
                statuses.append(
                    {
                        "cik": cik,
                        "accession_number": accession,
                        "status": "offline_missing",
                    }
                )
                continue
            else:
                try:
                    raw = fetcher(cik, accession, headers)
                    if not isinstance(raw, bytes) or not raw:
                        raise ValueError(
                            "SEC filing response must be non-empty bytes"
                        )
                    _atomic_write(path, raw)
                except Exception as exc:  # external SEC boundary
                    statuses.append(
                        {
                            "cik": cik,
                            "accession_number": accession,
                            "status": _provider_error_status(exc),
                        }
                    )
                    continue
            try:
                observation = parse_sec_submission_header(
                    raw.decode("utf-8", errors="replace"),
                    accession,
                    filing["filing_date"],
                    filing["acceptance_datetime"],
                )
            except ValueError:
                statuses.append(
                    {
                        "cik": cik,
                        "accession_number": accession,
                        "status": "invalid_header",
                    }
                )
                continue
            status = "missing_sic" if observation is None else "success"
            statuses.append(
                {
                    "cik": cik,
                    "accession_number": accession,
                    "status": status,
                    "reused": reused,
                }
            )
            if observation is not None:
                observations.append(
                    {
                        **observation,
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
    status_counts = {}
    for row in statuses:
        status_counts[row["status"]] = (
            status_counts.get(row["status"], 0) + 1
        )
    return {
        "confirmed_ciks": len(confirmed),
        "observations": tuple(
            sorted(
                observations,
                key=lambda row: (
                    row["cik"],
                    row["available_at"],
                    row["accession_number"],
                ),
            )
        ),
        "statuses": tuple(statuses),
        "status_counts": dict(sorted(status_counts.items())),
    }


def run_coverage_pilot(
    catalog_path,
    delisted_db,
    reference_db,
    raw_root,
    *,
    quotas,
    snapshot_date,
    sec_fetcher=None,
    sec_header_fetcher=None,
    provider_fetcher=None,
    token=None,
    offline=False,
):
    """Run the identity portion of the fixed-sample coverage pilot."""
    catalog_path = Path(catalog_path)
    raw_root = Path(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    catalog_bytes = catalog_path.read_bytes()
    catalog_sha256 = hashlib.sha256(catalog_bytes).hexdigest()
    catalog_payload = json.loads(catalog_bytes.decode("utf-8"))
    catalog = (
        catalog_payload.get("securities")
        if isinstance(catalog_payload, dict)
        else catalog_payload
    )
    if not isinstance(catalog, list):
        raise ValueError("purified catalog securities must be a list")
    history = _load_history_summaries(delisted_db)
    sample_path = raw_root / "sample.json"
    sample = _load_or_freeze_sample(
        sample_path,
        catalog,
        history,
        quotas,
        catalog_sha256,
    )
    sample_bytes = _canonical_json_bytes(list(sample))
    sample_sha256 = hashlib.sha256(sample_bytes).hexdigest()

    sec_cache_root = raw_root / "sec"
    if offline and not (
        (sec_cache_root / "submissions.zip").exists()
        and (sec_cache_root / "manifest.json").exists()
    ):
        raise ValueError("offline SEC submissions cache is missing")
    collect_artifact(
        "sec_submissions",
        sec_cache_root,
        fetcher=sec_fetcher,
    )
    sec_index = build_identity_index(
        iter_submission_records(raw_root / "sec" / "submissions.zip")
    )
    candidates_by_ticker = {}
    initial_decisions = []
    for row in sample:
        candidates = find_sec_candidates(row, sec_index)
        candidates_by_ticker[row["ticker"]] = candidates
        initial_decisions.append(
            adjudicate_identity(row, candidates, ())
        )

    provider_result = {
        "requested": 0,
        "statuses": (),
        "status_counts": {},
        "payloads": {},
    }
    if token or provider_fetcher is not None:
        provider_result = collect_provider_sample(
            initial_decisions,
            raw_root,
            token=token,
            fetcher=provider_fetcher,
            offline=offline,
        )
    final_decisions = []
    provider_snapshots = []
    for row, initial in zip(sample, initial_decisions):
        payload = provider_result["payloads"].get(row["ticker"])
        if payload is None:
            final_decisions.append(initial)
            continue
        general = payload.get("General") or {}
        evidence = normalize_provider_evidence(
            [
                {
                    "isin": general.get("ISIN"),
                    "cik": general.get("CIK"),
                    "code": general.get("Code") or row["ticker"],
                    "name": general.get("Name") or row["name"],
                }
            ],
            f"{snapshot_date}T00:00:00Z",
        )
        final_decisions.append(
            adjudicate_identity(
                row,
                candidates_by_ticker[row["ticker"]],
                evidence,
            )
        )
        provider_snapshots.append(
            {
                "ticker": row["ticker"],
                "sector": general.get("Sector"),
                "industry": general.get("Industry"),
                "snapshot_at": f"{snapshot_date}T00:00:00Z",
                "historical_eligibility": "snapshot_only",
                "source": "eodhd",
            }
        )
    header_result = collect_sec_header_sample(
        final_decisions,
        sec_index,
        raw_root,
        fetcher=sec_header_fetcher,
        offline=offline,
    )
    sic_intervals = (
        build_sic_intervals(header_result["observations"])
        if header_result["observations"]
        else ()
    )

    reference_db = Path(reference_db)
    temporary_db = reference_db.with_suffix(reference_db.suffix + ".tmp")
    if temporary_db.exists():
        temporary_db.unlink()
    with DelistedReferenceStore(temporary_db) as store:
        store.replace_sample(
            sample,
            catalog_sha256,
            snapshot_date,
        )
        store.replace_identity_results(final_decisions, ())
        if provider_snapshots:
            store.replace_provider_snapshots(provider_snapshots)
        if header_result["observations"]:
            store.replace_sic_observations(
                header_result["observations"],
                sic_intervals,
            )
        integrity = store.integrity_report()
        if integrity != {
            "integrity_check": "ok",
            "foreign_key_errors": 0,
        }:
            raise ValueError(f"reference database integrity failed: {integrity}")
    os.replace(temporary_db, reference_db)
    decision_counts = {}
    for row in final_decisions:
        status = row["link_status"]
        decision_counts[status] = decision_counts.get(status, 0) + 1
    return {
        "catalog_sha256": catalog_sha256,
        "sample_sha256": sample_sha256,
        "sample_count": len(sample),
        "decision_counts": dict(sorted(decision_counts.items())),
        "provider_status_counts": provider_result["status_counts"],
        "sic_status_counts": header_result["status_counts"],
        "sic_observation_count": len(header_result["observations"]),
        "reference_integrity": integrity,
        "decisions": tuple(final_decisions),
    }


def _verified_manifest(artifact_path, manifest_path):
    if not artifact_path.exists() or not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
        payload = artifact_path.read_bytes()
    except (OSError, json.JSONDecodeError):
        return None
    digest = hashlib.sha256(payload).hexdigest()
    if (
        manifest.get("sha256") != digest
        or int(manifest.get("byte_count") or -1) != len(payload)
    ):
        return None
    return manifest


def _atomic_write(path, payload):
    path = Path(path)
    with tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _fetch_url(url, headers):
    request = Request(url, headers=dict(headers))
    with urlopen(request, timeout=90) as response:
        return response.read()


def _fetch_eodhd_fundamentals(ticker, token):
    if not str(token or ""):
        raise ValueError("EODHD_API_TOKEN is required")
    query = urlencode({"api_token": token, "fmt": "json"})
    url = (
        "https://eodhd.com/api/fundamentals/"
        + quote(ticker, safe=".-")
        + ".US?"
        + query
    )
    with urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_sec_submission(cik, accession, headers):
    compact = accession.replace("-", "")
    url = (
        "https://www.sec.gov/Archives/edgar/data/"
        + str(int(cik))
        + "/"
        + compact
        + "/"
        + accession
        + ".txt"
    )
    request = Request(url, headers=dict(headers))
    with urlopen(request, timeout=90) as response:
        return response.read()


def _provider_error_status(exc):
    if isinstance(exc, HTTPError):
        if exc.code in {401, 403}:
            return "authorization_error"
        if exc.code == 404:
            return "not_found"
        if exc.code == 429:
            return "quota_exhausted"
        if 500 <= exc.code <= 599:
            return "transient_error"
        return "permanent_error"
    if isinstance(exc, (URLError, TimeoutError)):
        return "transient_error"
    return "invalid_response"


def _cached_json_object(path):
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"cached provider response is corrupt: {path.name}")
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"cached provider response is corrupt: {path.name}")
    return payload


def _load_history_summaries(path):
    uri = f"file:{Path(path).resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            """
            SELECT ticker, min(first_date), max(last_date), sum(row_count)
            FROM history_segments
            GROUP BY ticker
            """
        ).fetchall()
    finally:
        connection.close()
    return {
        str(ticker).upper(): {
            "first_date": first_date,
            "last_date": last_date,
            "valid_rows": int(row_count or 0),
        }
        for ticker, first_date, last_date, row_count in rows
    }


def _load_or_freeze_sample(
    path,
    catalog,
    history,
    quotas,
    catalog_sha256,
):
    if path.exists():
        payload = json.loads(path.read_text())
        if (
            payload.get("sample_version") != SAMPLE_VERSION
            or payload.get("catalog_sha256") != catalog_sha256
            or payload.get("quotas") != quotas
        ):
            raise ValueError("frozen sample provenance does not match")
        return tuple(payload["securities"])
    selected = select_coverage_sample(catalog, history, quotas)
    enriched = tuple(
        {
            **row,
            "first_date": (history.get(row["ticker"]) or {}).get(
                "first_date"
            ),
        }
        for row in selected
    )
    payload = {
        "sample_version": SAMPLE_VERSION,
        "catalog_sha256": catalog_sha256,
        "quotas": quotas,
        "securities": enriched,
    }
    _atomic_write(path, _canonical_json_bytes(payload))
    return enriched


def _canonical_json_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _edge_sample(rows, count):
    if count <= 0 or not rows:
        return []
    if len(rows) <= count:
        return list(rows)
    if count == 1:
        return [rows[-1]]
    indexes = {
        round(index * (len(rows) - 1) / (count - 1))
        for index in range(count)
    }
    return [rows[index] for index in sorted(indexes)]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the delisted identity coverage pilot."
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--delisted-db", required=True)
    parser.add_argument("--reference-db", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--snapshot-date", default="2026-07-27")
    parser.add_argument("--offline", action="store_true")
    arguments = parser.parse_args(argv)
    result = run_coverage_pilot(
        arguments.catalog,
        arguments.delisted_db,
        arguments.reference_db,
        arguments.raw_root,
        quotas=DEFAULT_QUOTAS,
        snapshot_date=arguments.snapshot_date,
        token=os.environ.get("EODHD_API_TOKEN"),
        offline=arguments.offline,
    )
    printable = {
        key: value
        for key, value in result.items()
        if key != "decisions"
    }
    print(
        json.dumps(
            printable,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _validate_sec_submissions_zip(payload):
    try:
        with ZipFile(BytesIO(payload)) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.startswith("CIK") and name.endswith(".json")
            ]
            if not names:
                raise ValueError(
                    "SEC submissions artifact must contain CIK JSON files"
                )
            json.loads(archive.read(names[0]).decode("utf-8"))
    except (BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(
            "SEC submissions artifact must be a valid ZIP"
        ) from None


if __name__ == "__main__":
    raise SystemExit(main())
