"""Point-in-time, auditable stock sector and theme assignments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from web.market_groups import MARKET_GROUPS, SECTOR_ETFS


DEFAULT_OVERRIDES_PATH = Path(__file__).with_name("security_group_overrides_v1.json")
REVIEW_SECTOR_KEY = "unclassified_review"
_STANDARD_SECTORS = frozenset(SECTOR_ETFS)
_THEME_BENCHMARKS = MappingProxyType(
    {
        key: tuple(group.benchmark_tickers)
        for key, group in MARKET_GROUPS.items()
        if key not in SECTOR_ETFS and group.benchmark_tickers
    }
)
_OVERRIDE_FIELDS = frozenset(
    {
        "ticker",
        "effective_from",
        "effective_to",
        "sector_key",
        "theme_keys",
        "primary_model_group",
        "reason",
        "rule_version",
    }
)


@dataclass(frozen=True)
class GroupAssignment:
    ticker: str
    asof: str
    sector_key: str
    sector_benchmark: str | None
    theme_keys: tuple[str, ...]
    theme_benchmarks: Mapping[str, tuple[str, ...]]
    primary_model_group: str
    classification_state: str
    source: str
    rule_version: str
    confidence: float
    override_reason: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "theme_keys", tuple(self.theme_keys))
        object.__setattr__(
            self,
            "theme_benchmarks",
            MappingProxyType(
                {key: tuple(value) for key, value in self.theme_benchmarks.items()}
            ),
        )


def load_group_overrides(path=None):
    """Load and validate versioned manual assignment exceptions."""
    source = DEFAULT_OVERRIDES_PATH if path is None else Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        version = payload.get("rule_version")
        entries = payload.get("overrides", ())
    elif isinstance(payload, list):
        version = None
        entries = payload
    else:
        raise ValueError("invalid_group_overrides")
    if not isinstance(entries, list):
        raise ValueError("invalid_group_overrides")

    normalized = tuple(
        _normalize_override(entry, default_rule_version=version) for entry in entries
    )
    _validate_non_overlapping_ranges(normalized)
    return normalized


def resolve_group_assignment(ticker, classifications, asof, overrides=None):
    """Resolve override -> SEC exact/theme -> SEC broad -> behavior -> review."""
    normalized_ticker = str(ticker).strip().upper()
    observation_date = _normalize_date(asof)
    records = load_group_overrides() if overrides is None else tuple(
        _normalize_override(entry) for entry in overrides
    )
    _validate_non_overlapping_ranges(records)
    override = _active_override(records, normalized_ticker, observation_date)
    if override is not None:
        return _assignment_from_override(normalized_ticker, observation_date, override)

    evidence = classifications or {}
    sec = evidence.get("sec") or {}
    sec_sector = sec.get("sector_key")
    if sec_sector in _STANDARD_SECTORS:
        themes = _normalize_themes(sec.get("theme_keys", ()))
        return _classified_assignment(
            normalized_ticker,
            observation_date,
            sec_sector,
            themes,
            "sec_exact" if themes else "sec_broad",
            str(sec.get("rule_version") or "sec_sic_v1"),
            _confidence(sec.get("confidence")),
        )

    behavior = evidence.get("market_behavior") or {}
    behavior_sector = behavior.get("sector_key")
    if behavior_sector in _STANDARD_SECTORS:
        return _classified_assignment(
            normalized_ticker,
            observation_date,
            behavior_sector,
            (),
            "market_behavior",
            str(behavior.get("rule_version") or "market_behavior_v1"),
            _confidence(behavior.get("confidence")),
        )

    return GroupAssignment(
        ticker=normalized_ticker,
        asof=observation_date,
        sector_key=REVIEW_SECTOR_KEY,
        sector_benchmark=None,
        theme_keys=(),
        theme_benchmarks={},
        primary_model_group=REVIEW_SECTOR_KEY,
        classification_state="needs_review",
        source="review",
        rule_version="group_assignment_v1",
        confidence=0.0,
    )


def audit_assignments(assignments):
    """Return deterministic coverage and integrity findings for assignments."""
    records = tuple(assignments)
    invalid_benchmarks = set()
    duplicate_themes = set()
    by_identity = {}
    theme_counts = {}
    needs_review_count = 0

    valid_benchmarks = {
        *SECTOR_ETFS.values(),
        *(ticker for values in _THEME_BENCHMARKS.values() for ticker in values),
    }
    for assignment in records:
        if assignment.classification_state == "needs_review":
            needs_review_count += 1
        if assignment.sector_benchmark is not None and (
            assignment.sector_benchmark not in valid_benchmarks
        ):
            invalid_benchmarks.add(assignment.sector_benchmark)
        if len(set(assignment.theme_keys)) != len(assignment.theme_keys):
            duplicate_themes.add(assignment.ticker)
        for theme in assignment.theme_keys:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        for values in assignment.theme_benchmarks.values():
            invalid_benchmarks.update(
                ticker for ticker in values if ticker not in valid_benchmarks
            )
        key = (assignment.ticker, assignment.asof)
        by_identity.setdefault(key, []).append(assignment)

    conflicts = sorted(
        {
            ticker
            for (ticker, _), values in by_identity.items()
            if len({(item.sector_key, item.theme_keys, item.primary_model_group) for item in values})
            > 1
        }
    )
    total = len(records)
    return {
        "total": total,
        "assigned": total,
        "coverage": 1.0 if total else 0.0,
        "needs_review_count": needs_review_count,
        "invalid_benchmarks": sorted(invalid_benchmarks),
        "duplicate_themes": sorted(duplicate_themes),
        "conflicting_assignments": conflicts,
        "theme_counts": dict(sorted(theme_counts.items())),
    }


def _assignment_from_override(ticker, asof, override):
    return _classified_assignment(
        ticker,
        asof,
        override["sector_key"],
        tuple(override["theme_keys"]),
        "override",
        override["rule_version"],
        1.0,
        override_reason=override["reason"],
        primary_model_group=override["primary_model_group"],
    )


def _classified_assignment(
    ticker,
    asof,
    sector_key,
    theme_keys,
    source,
    rule_version,
    confidence,
    *,
    override_reason=None,
    primary_model_group=None,
):
    themes = _normalize_themes(theme_keys)
    return GroupAssignment(
        ticker=ticker,
        asof=asof,
        sector_key=sector_key,
        sector_benchmark=SECTOR_ETFS[sector_key],
        theme_keys=themes,
        theme_benchmarks={theme: _THEME_BENCHMARKS[theme] for theme in themes},
        primary_model_group=primary_model_group or (themes[0] if themes else sector_key),
        classification_state="classified",
        source=source,
        rule_version=rule_version,
        confidence=confidence,
        override_reason=override_reason,
    )


def _normalize_override(entry, default_rule_version=None):
    if not isinstance(entry, dict) or not _OVERRIDE_FIELDS <= entry.keys():
        raise ValueError("invalid_group_override")
    ticker = str(entry["ticker"]).strip().upper()
    start = _normalize_date(entry["effective_from"])
    finish = _normalize_date(entry["effective_to"])
    if start > finish or entry["sector_key"] not in _STANDARD_SECTORS:
        raise ValueError("invalid_group_override")
    themes = _normalize_themes(entry["theme_keys"])
    primary = str(entry["primary_model_group"])
    if primary not in {entry["sector_key"], *themes}:
        raise ValueError("invalid_group_override")
    return MappingProxyType(
        {
            "ticker": ticker,
            "effective_from": start,
            "effective_to": finish,
            "sector_key": entry["sector_key"],
            "theme_keys": themes,
            "primary_model_group": primary,
            "reason": str(entry["reason"]),
            "rule_version": str(entry.get("rule_version") or default_rule_version or "group_override_v1"),
        }
    )


def _validate_non_overlapping_ranges(overrides):
    by_ticker = {}
    for override in overrides:
        by_ticker.setdefault(override["ticker"], []).append(override)
    for records in by_ticker.values():
        records.sort(key=lambda item: item["effective_from"])
        for previous, current in zip(records, records[1:]):
            if current["effective_from"] <= previous["effective_to"]:
                raise ValueError("conflicting_override_effective_ranges")


def _active_override(overrides, ticker, asof):
    return next(
        (
            override
            for override in overrides
            if override["ticker"] == ticker
            and override["effective_from"] <= asof <= override["effective_to"]
        ),
        None,
    )


def _normalize_themes(themes):
    normalized = tuple(str(theme) for theme in (themes or ()))
    if len(set(normalized)) != len(normalized) or any(
        theme not in _THEME_BENCHMARKS for theme in normalized
    ):
        raise ValueError("invalid_theme_keys")
    return normalized


def _normalize_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError("invalid_asof_date") from exc


def _confidence(value):
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
