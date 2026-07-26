"""Inspect and safely maintain the disposable local analysis cache."""

from __future__ import annotations

import argparse
from hashlib import blake2b
import json
from pathlib import Path
import sqlite3
import sys

import build_forecast_cache


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = PROJECT_ROOT / "data" / "analysis_cache.db"
_TABLES = {
    "forecast_artifacts": 32,
    "entry_signal_artifacts": 20,
}


def _positive_integer(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("retention must be at least 1")
    return parsed


def _parser():
    parser = argparse.ArgumentParser(
        description="Inspect and maintain the disposable analysis cache."
    )
    parser.add_argument(
        "--cache",
        default=str(DEFAULT_CACHE),
        help="Path to the analysis cache SQLite database.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Report entries and disk usage.")
    commands.add_parser("verify", help="Verify every payload checksum.")

    prune = commands.add_parser(
        "prune",
        help="Preview or apply bounded retention cleanup.",
    )
    prune.add_argument(
        "--forecast-keep",
        type=_positive_integer,
        default=2,
        help="Number of newest forecast artifacts to retain.",
    )
    prune.add_argument(
        "--entry-keep",
        type=_positive_integer,
        default=64,
        help="Number of newest entry-signal artifacts to retain.",
    )
    prune.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletion; without this flag prune is a dry run.",
    )

    prewarm = commands.add_parser(
        "prewarm",
        help="Reuse the forecast cache builder for manual prewarming.",
    )
    prewarm.add_argument(
        "--database",
        required=True,
        help="Path to the read-only prices SQLite database.",
    )
    return parser


def _disk_usage(path):
    candidates = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    return sum(item.stat().st_size for item in candidates if item.exists())


def _read_connection(path):
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _table_exists(connection, table):
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone() is not None


def _table_summary(connection, table):
    if not _table_exists(connection, table):
        return {
            "entry_count": 0,
            "payload_bytes": 0,
            "oldest_created_at": None,
            "latest_created_at": None,
        }
    row = connection.execute(
        f"""
        SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0),
               MIN(created_at), MAX(created_at)
        FROM {table}
        """
    ).fetchone()
    return {
        "entry_count": int(row[0]),
        "payload_bytes": int(row[1]),
        "oldest_created_at": row[2],
        "latest_created_at": row[3],
    }


def cache_status(path):
    path = Path(path)
    if not path.exists():
        tables = {
            table: _table_summary_missing()
            for table in _TABLES
        }
        return {
            "status": "empty",
            "cache_path": str(path),
            "disk_usage_bytes": 0,
            "payload_bytes": 0,
            "tables": tables,
        }
    with _read_connection(path) as connection:
        tables = {
            table: _table_summary(connection, table)
            for table in _TABLES
        }
    payload_bytes = sum(item["payload_bytes"] for item in tables.values())
    return {
        "status": "ready",
        "cache_path": str(path),
        "disk_usage_bytes": _disk_usage(path),
        "payload_bytes": payload_bytes,
        "tables": tables,
    }


def _table_summary_missing():
    return {
        "entry_count": 0,
        "payload_bytes": 0,
        "oldest_created_at": None,
        "latest_created_at": None,
    }


def verify_cache(path):
    path = Path(path)
    invalid = []
    checked_by_table = {table: 0 for table in _TABLES}
    if path.exists():
        with _read_connection(path) as connection:
            for table, digest_size in _TABLES.items():
                if not _table_exists(connection, table):
                    continue
                cursor = connection.execute(
                    f"""
                    SELECT cache_key, payload_checksum, payload
                    FROM {table}
                    ORDER BY created_at DESC, rowid DESC
                    """
                )
                for cache_key, expected, payload in cursor:
                    checked_by_table[table] += 1
                    actual = blake2b(
                        bytes(payload),
                        digest_size=digest_size,
                    ).hexdigest()
                    if actual != expected:
                        invalid.append(
                            {
                                "table": table,
                                "cache_key": cache_key,
                            }
                        )
    checked = sum(checked_by_table.values())
    return {
        "status": "ok" if not invalid else "corrupt",
        "checked_entries": checked,
        "checked_by_table": checked_by_table,
        "invalid_count": len(invalid),
        "invalid_entries": invalid,
    }


def _expired_keys(connection, table, keep):
    if not _table_exists(connection, table):
        return []
    return [
        row[0]
        for row in connection.execute(
            f"""
            SELECT cache_key
            FROM {table}
            ORDER BY created_at DESC, rowid DESC
            LIMIT -1 OFFSET ?
            """,
            (keep,),
        )
    ]


def prune_cache(path, forecast_keep=2, entry_keep=64, apply=False):
    path = Path(path)
    keep_by_table = {
        "forecast_artifacts": forecast_keep,
        "entry_signal_artifacts": entry_keep,
    }
    keys_by_table = {table: [] for table in _TABLES}
    if path.exists():
        if apply:
            with sqlite3.connect(path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                keys_by_table = {
                    table: _expired_keys(connection, table, keep)
                    for table, keep in keep_by_table.items()
                }
                for table, keys in keys_by_table.items():
                    connection.executemany(
                        f"DELETE FROM {table} WHERE cache_key = ?",
                        ((key,) for key in keys),
                    )
        else:
            with _read_connection(path) as connection:
                keys_by_table = {
                    table: _expired_keys(connection, table, keep)
                    for table, keep in keep_by_table.items()
                }
    counts = {
        table: len(keys)
        for table, keys in keys_by_table.items()
    }
    status = cache_status(path)
    payload = {
        "status": "pruned" if apply else "dry_run",
        "retention": keep_by_table,
        "remaining": {
            table: summary["entry_count"]
            for table, summary in status["tables"].items()
        },
    }
    payload["removed" if apply else "would_remove"] = counts
    return payload


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "prewarm":
        return build_forecast_cache.main(
            [
                "--database",
                args.database,
                "--cache",
                args.cache,
            ]
        )
    try:
        if args.command == "status":
            payload = cache_status(args.cache)
            exit_code = 0
        elif args.command == "verify":
            payload = verify_cache(args.cache)
            exit_code = 0 if payload["status"] == "ok" else 2
        else:
            payload = prune_cache(
                args.cache,
                forecast_keep=args.forecast_keep,
                entry_keep=args.entry_keep,
                apply=args.apply,
            )
            exit_code = 0
    except (OSError, sqlite3.Error, TypeError, ValueError):
        payload = {"error": "analysis_cache_maintenance_failed"}
        exit_code = 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
