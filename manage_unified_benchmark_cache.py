"""Inspect, verify, and safely prune the unified benchmark cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.unified_benchmark_cache import UnifiedBenchmarkCacheStore


DEFAULT_DATABASE = Path("data/unified_benchmark_cache.db")


def _parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--database",
            type=Path,
            default=DEFAULT_DATABASE,
        )
    prune = subparsers.add_parser("prune")
    prune.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
    )
    prune.add_argument("--keep-per-stage", type=int, default=3)
    prune.add_argument("--apply", action="store_true")
    return parser


def _status(store):
    rows = store.status()
    counts = (
        {}
        if rows.empty
        else {
            str(stage): int(count)
            for stage, count in rows.groupby("stage", sort=True).size().items()
        }
    )
    return {
        "ok": True,
        "command": "status",
        "artifact_count": int(len(rows)),
        "payload_size_bytes": int(rows["payload_size_bytes"].sum())
        if not rows.empty
        else 0,
        "row_count": int(rows["row_count"].sum()) if not rows.empty else 0,
        "stage_counts": counts,
    }


def _verify(store):
    rows = store.verify()
    invalid = (
        rows.loc[rows["status"] != "valid"] if not rows.empty else rows
    )
    return {
        "ok": bool(invalid.empty),
        "command": "verify",
        "artifact_count": int(len(rows)),
        "valid_count": int((rows["status"] == "valid").sum())
        if not rows.empty
        else 0,
        "invalid_count": int(len(invalid)),
        "results": (
            []
            if rows.empty
            else rows.fillna("").to_dict(orient="records")
        ),
    }


def _prune(store, *, keep_per_stage, apply):
    rows = store.prune(keep_per_stage=keep_per_stage, apply=apply)
    would_delete = rows.loc[rows["would_delete"]] if not rows.empty else rows
    deleted = rows.loc[rows["deleted"]] if not rows.empty else rows
    selected = deleted if apply else would_delete
    return {
        "ok": True,
        "command": "prune",
        "apply": bool(apply),
        "keep_per_stage": int(keep_per_stage),
        "would_delete_count": int(len(would_delete)),
        "deleted_count": int(len(deleted)),
        "selected_row_count": int(selected["row_count"].sum())
        if not selected.empty
        else 0,
        "selected_payload_size_bytes": int(
            selected["payload_size_bytes"].sum()
        )
        if not selected.empty
        else 0,
        "artifact_keys": (
            [] if selected.empty else selected["artifact_key"].astype(str).tolist()
        ),
    }


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        store = UnifiedBenchmarkCacheStore(arguments.database)
        if arguments.command == "status":
            payload = _status(store)
        elif arguments.command == "verify":
            payload = _verify(store)
        else:
            payload = _prune(
                store,
                keep_per_stage=arguments.keep_per_stage,
                apply=arguments.apply,
            )
        exit_code = 0 if payload["ok"] else 1
    except Exception:
        payload = {
            "ok": False,
            "error_code": "benchmark_cache_command_failed",
        }
        exit_code = 1
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
