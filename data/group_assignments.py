"""Point-in-time, auditable stock sector and theme assignments."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
import json
from pathlib import Path
from types import MappingProxyType

from web.market_groups import MARKET_GROUPS, SECTOR_ETFS


DEFAULT_OVERRIDES_PATH = Path(__file__).with_name("security_group_overrides_v1.json")
REVIEW_SECTOR_KEY = "unclassified_review"
HISTORICAL_BACKFILL_RULE_PREFIX = "historical_backfill_v1/"
HISTORICAL_BACKFILL_SOURCE_PREFIX = "historical_backfill_assumption/"
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
    effective_from: str | None = None
    effective_to: str | None = None

    def __post_init__(self):
        effective_from = _normalize_date(self.effective_from or self.asof)
        effective_to = (
            None
            if self.effective_to is None
            else _normalize_date(self.effective_to)
        )
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("invalid_assignment_effective_range")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
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


def historical_group_assignment_intervals(
    ticker,
    classifications,
    *,
    observed_at,
    evidence_start,
    overrides=None,
):
    """Backfill current classification only across evidenced identity history.

    This is deliberately an assumption rather than reconstructed taxonomy
    history. Manual override intervals retain their explicit boundaries.
    """
    observation_date = _normalize_date(observed_at)
    history_start = _normalize_date(evidence_start)
    if history_start > observation_date:
        raise ValueError("assignment_evidence_after_observation")
    records = (
        load_group_overrides()
        if overrides is None
        else tuple(_normalize_override(entry) for entry in overrides)
    )
    _validate_non_overlapping_ranges(records)
    normalized_ticker = str(ticker).strip().upper()
    baseline = resolve_group_assignment(
        normalized_ticker,
        classifications,
        observation_date,
        overrides=(),
    )

    def assumed(start, finish):
        return replace(
            baseline,
            asof=observation_date,
            source=HISTORICAL_BACKFILL_SOURCE_PREFIX + baseline.source,
            rule_version=HISTORICAL_BACKFILL_RULE_PREFIX + baseline.rule_version,
            effective_from=start,
            effective_to=finish,
        )

    applicable = sorted(
        (
            record
            for record in records
            if record["ticker"] == normalized_ticker
            and record["effective_from"] <= observation_date
            and history_start < record["effective_to"]
        ),
        key=lambda record: record["effective_from"],
    )
    assignments = []
    cursor = history_start
    for override in applicable:
        override_start = max(history_start, override["effective_from"])
        if cursor < override_start:
            assignments.append(assumed(cursor, override_start))
        effective_start = max(cursor, override_start)
        if effective_start < override["effective_to"]:
            assignments.append(
                replace(
                    _assignment_from_override(
                        normalized_ticker,
                        observation_date,
                        override,
                    ),
                    effective_from=effective_start,
                )
            )
        cursor = max(cursor, override["effective_to"])
    if cursor <= observation_date:
        assignments.append(assumed(cursor, None))
    return tuple(assignments)


def audit_assignments(assignments):
    """Return deterministic coverage and integrity findings for assignments."""
    records = tuple(assignments)
    invalid_benchmarks = set()
    invalid_benchmark_mappings = {}
    duplicate_themes = set()
    invalid_states = set()
    invalid_primary_groups = set()
    by_ticker = {}
    for assignment in records:
        by_ticker.setdefault(assignment.ticker, []).append(assignment)
        expected_sector_benchmark = SECTOR_ETFS.get(assignment.sector_key)
        is_review = assignment.sector_key == REVIEW_SECTOR_KEY
        if assignment.sector_key not in SECTOR_ETFS and not is_review:
            finding = {
                "actual": assignment.sector_benchmark,
                "expected": None,
                "group": assignment.sector_key,
                "kind": "sector",
                "reason": "unknown_group",
                "ticker": assignment.ticker,
            }
            invalid_benchmark_mappings[
                (
                    assignment.ticker,
                    "sector",
                    assignment.sector_key,
                    "unknown_group",
                )
            ] = finding
        elif assignment.sector_benchmark != expected_sector_benchmark:
            finding = {
                "actual": assignment.sector_benchmark,
                "expected": expected_sector_benchmark,
                "group": assignment.sector_key,
                "kind": "sector",
                "ticker": assignment.ticker,
            }
            invalid_benchmark_mappings[
                (assignment.ticker, "sector", assignment.sector_key, "")
            ] = finding
        if assignment.sector_benchmark != expected_sector_benchmark:
            invalid_benchmarks.add(str(assignment.sector_benchmark))
        if len(set(assignment.theme_keys)) != len(assignment.theme_keys):
            duplicate_themes.add(assignment.ticker)
        for theme in sorted(
            set(assignment.theme_keys) | set(assignment.theme_benchmarks)
        ):
            actual = assignment.theme_benchmarks.get(theme)
            expected = _THEME_BENCHMARKS.get(theme)
            if theme not in _THEME_BENCHMARKS:
                finding = {
                    "actual": actual,
                    "expected": None,
                    "group": theme,
                    "kind": "theme",
                    "reason": "unknown_group",
                    "ticker": assignment.ticker,
                }
                invalid_benchmark_mappings[
                    (assignment.ticker, "theme", theme, "unknown_group")
                ] = finding
            elif theme not in assignment.theme_keys or actual != expected:
                finding = {
                    "actual": actual,
                    "expected": expected,
                    "group": theme,
                    "kind": "theme",
                    "ticker": assignment.ticker,
                }
                invalid_benchmark_mappings[
                    (assignment.ticker, "theme", theme, "")
                ] = finding
                invalid_benchmarks.update(
                    str(value)
                    for value in assignment.theme_benchmarks.get(theme, ())
                )
        expected_state = "needs_review" if is_review else "classified"
        if assignment.classification_state != expected_state:
            invalid_states.add(assignment.ticker)
        allowed_primary = (
            {REVIEW_SECTOR_KEY}
            if is_review
            else {assignment.sector_key, *assignment.theme_keys}
        )
        if assignment.primary_model_group not in allowed_primary:
            invalid_primary_groups.add(assignment.ticker)

    conflicts = set()
    for ticker, values in by_ticker.items():
        ordered = sorted(
            values,
            key=lambda assignment: (
                assignment.effective_from,
                assignment.rule_version,
            ),
        )
        outer = None
        for assignment in ordered:
            if outer is not None and (
                outer.effective_to is None
                or assignment.effective_from < outer.effective_to
            ):
                conflicts.add(ticker)
            if (
                outer is None
                or (
                    outer.effective_to is not None
                    and (
                        assignment.effective_to is None
                        or outer.effective_to < assignment.effective_to
                    )
                )
            ):
                outer = assignment

    observation_date = max(
        (assignment.asof for assignment in records),
        default=None,
    )
    current = {
        ticker: next(
            (
                assignment
                for assignment in sorted(
                    values,
                    key=lambda item: (
                        item.effective_from,
                        item.rule_version,
                    ),
                    reverse=True,
                )
                if observation_date is not None
                and assignment.effective_from <= observation_date
                and (
                    assignment.effective_to is None
                    or observation_date < assignment.effective_to
                )
            ),
            None,
        )
        for ticker, values in by_ticker.items()
    }
    theme_counts = {}
    needs_review_count = 0
    for assignment in current.values():
        if assignment is None:
            continue
        if assignment.classification_state == "needs_review":
            needs_review_count += 1
        for theme in assignment.theme_keys:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
    total = len(by_ticker)
    return {
        "total": total,
        "assigned": total,
        "coverage": 1.0 if total else 0.0,
        "needs_review_count": needs_review_count,
        "invalid_benchmarks": sorted(
            value for value in invalid_benchmarks if value != "None"
        ),
        "invalid_benchmark_mappings": [
            invalid_benchmark_mappings[key]
            for key in sorted(invalid_benchmark_mappings)
        ],
        "duplicate_themes": sorted(duplicate_themes),
        "conflicting_assignments": sorted(conflicts),
        "invalid_classification_states": sorted(invalid_states),
        "invalid_primary_model_groups": sorted(invalid_primary_groups),
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
        effective_from=override["effective_from"],
        effective_to=override["effective_to"],
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
    effective_from=None,
    effective_to=None,
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
        effective_from=effective_from,
        effective_to=effective_to,
    )


def _normalize_override(entry, default_rule_version=None):
    if not isinstance(entry, Mapping) or not _OVERRIDE_FIELDS <= entry.keys():
        raise ValueError("invalid_group_override")
    ticker = str(entry["ticker"]).strip().upper()
    start = _normalize_date(entry["effective_from"])
    finish = _normalize_date(entry["effective_to"])
    if start >= finish or entry["sector_key"] not in _STANDARD_SECTORS:
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
            if current["effective_from"] < previous["effective_to"]:
                raise ValueError("conflicting_override_effective_ranges")


def _active_override(overrides, ticker, asof):
    return next(
        (
            override
            for override in overrides
            if override["ticker"] == ticker
            and override["effective_from"] <= asof < override["effective_to"]
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
