"""Resumably collect EODHD split and dividend events for a catalog."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from web.market_groups import REFERENCE_TICKERS


def fetch_json(url, *, retries=4):
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("EODHD action response must be a list")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            if attempt + 1 >= retries:
                raise
            time.sleep(min(8, 2**attempt))


def collect_one(ticker, kind, output, token, start, finish):
    if output.exists():
        payload = json.loads(output.read_text())
        if isinstance(payload, list):
            return "cached", len(payload)
    query = urlencode(
        {
            "api_token": token,
            "fmt": "json",
            "from": start,
            "to": finish,
        }
    )
    endpoint = {"splits": "splits", "dividends": "div"}[kind]
    payload = fetch_json(
        f"https://eodhd.com/api/{endpoint}/{ticker}.US?{query}"
    )
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    temporary.replace(output)
    return "downloaded", len(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        default="data/cache/research_universe_liquid100m_v1.json",
    )
    parser.add_argument(
        "--raw-root", default="data/cache/eodhd_raw/2026-07-26"
    )
    parser.add_argument("--from-date", default="2016-07-26")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    token = os.environ.get("EODHD_API_TOKEN")
    if not token:
        raise SystemExit("EODHD_API_TOKEN is required")
    catalog = json.loads(Path(args.catalog).read_text())
    tickers = sorted(
        {row["ticker"] for row in catalog["securities"]} | set(REFERENCE_TICKERS)
    )
    root = Path(args.raw_root)
    for kind in ("splits", "dividends"):
        (root / kind).mkdir(parents=True, exist_ok=True)
    jobs = [
        (
            ticker,
            kind,
            root / kind / f"{ticker}.json",
            token,
            args.from_date,
            catalog["asof"],
        )
        for ticker in tickers
        for kind in ("splits", "dividends")
    ]
    totals = {"cached": 0, "downloaded": 0, "events": 0}
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_map = {
            pool.submit(collect_one, *job): (job[0], job[1]) for job in jobs
        }
        for index, future in enumerate(as_completed(future_map), 1):
            ticker, kind = future_map[future]
            try:
                status, count = future.result()
                totals[status] += 1
                totals["events"] += count
            except Exception as exc:  # pragma: no cover - network boundary
                errors.append(f"{ticker}:{kind}:{type(exc).__name__}:{exc}")
            if index % 250 == 0:
                print(
                    f"{index}/{len(jobs)} downloaded={totals['downloaded']} "
                    f"cached={totals['cached']} errors={len(errors)}",
                    flush=True,
                )
    print(json.dumps({**totals, "errors": errors}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
