"""Collect immutable EODHD point-in-time universe source snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from data.point_in_time_universe import (
    normalize_historical_components,
    normalize_symbol_changes,
)


COMPONENTS_ENDPOINT = "https://eodhd.com/api/fundamentals/GSPC.INDX"
SYMBOL_CHANGES_ENDPOINT = "https://eodhd.com/api/symbol-change-history"


def fetch_json(url, *, retries=4):
    for attempt in range(int(retries)):
        try:
            with urlopen(url, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            if attempt + 1 >= int(retries):
                raise
            time.sleep(min(8, 2**attempt))


def collect_snapshot(
    output_root,
    *,
    snapshot_date,
    collected_at=None,
    input_components=None,
    input_symbol_changes=None,
    token=None,
    from_date="2016-01-01",
):
    """Collect or validate both feeds, then atomically write one snapshot."""
    output_root = Path(output_root)
    snapshot_date = str(snapshot_date)
    collected_at = collected_at or datetime.now(timezone.utc).isoformat()
    offline = input_components is not None or input_symbol_changes is not None
    if offline and (
        input_components is None or input_symbol_changes is None
    ):
        raise ValueError("offline mode requires both input files")

    if offline:
        components = json.loads(Path(input_components).read_text())
        changes = json.loads(Path(input_symbol_changes).read_text())
    else:
        token = str(token or os.environ.get("EODHD_API_TOKEN") or "")
        if not token:
            raise ValueError("EODHD_API_TOKEN is required")
        components = fetch_json(
            COMPONENTS_ENDPOINT
            + "?"
            + urlencode(
                {
                    "api_token": token,
                    "fmt": "json",
                    "filter": "HistoricalTickerComponents",
                }
            )
        )
        changes = fetch_json(
            SYMBOL_CHANGES_ENDPOINT
            + "?"
            + urlencode(
                {
                    "api_token": token,
                    "fmt": "json",
                    "ex": "US",
                    "from": str(from_date),
                    "to": snapshot_date,
                }
            )
        )

    memberships = normalize_historical_components(components)
    symbol_changes = normalize_symbol_changes(changes)
    output_root.mkdir(parents=True, exist_ok=True)
    component_path = output_root / "historical_components.json"
    change_path = output_root / "symbol_changes.json"
    _atomic_json(component_path, components)
    _atomic_json(change_path, changes)
    manifest = {
        "schema_version": "point_in_time_universe_snapshot_v1",
        "snapshot_date": snapshot_date,
        "collected_at": str(collected_at),
        "mode": "offline" if offline else "eodhd",
        "universe_key": "sp500_historical_eodhd_v1",
        "component_count": len(memberships),
        "symbol_change_count": len(symbol_changes),
        "sources": {
            "historical_components": COMPONENTS_ENDPOINT,
            "symbol_changes": SYMBOL_CHANGES_ENDPOINT,
        },
        "files": {
            "historical_components.json": _sha256(component_path),
            "symbol_changes.json": _sha256(change_path),
        },
    }
    _atomic_json(output_root / "manifest.json", manifest)
    return manifest


def _atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--from-date", default="2016-01-01")
    parser.add_argument("--input-components")
    parser.add_argument("--input-symbol-changes")
    args = parser.parse_args(argv)
    manifest = collect_snapshot(
        args.output_root,
        snapshot_date=args.snapshot_date,
        input_components=args.input_components,
        input_symbol_changes=args.input_symbol_changes,
        from_date=args.from_date,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
