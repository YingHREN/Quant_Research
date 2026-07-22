"""Common factor protocol and serializable evaluation result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from web.contracts import iso_date, json_safe
from web.services.analysis import AnalysisContext


class FactorDefinition(Protocol):
    """Contract implemented by dashboard factor plug-ins."""

    key: str
    label: str
    group: str
    direction: str
    description: str
    version: str

    def compute(self, context: AnalysisContext) -> Any:
        """Return the point-in-time raw factor value, or ``None`` if unavailable."""

    def format(self, value: Any) -> str:
        """Return a user-facing rendering of a non-missing raw value."""


@dataclass(frozen=True)
class FactorResult:
    key: str
    label: str
    group: str
    direction: str
    raw_value: Any
    formatted: str | None
    observation_date: Any
    missing: bool
    missing_reason: str | None
    description: str
    version: str
    percentile: float | None = None
    peer_count: int | None = None
    display_score: float | None = None

    def to_dict(self):
        """Return the stable, JSON-safe factor response shape."""
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "direction": self.direction,
            "raw_value": json_safe(self.raw_value),
            "formatted": self.formatted,
            "percentile": json_safe(self.percentile),
            "peer_count": self.peer_count,
            "display_score": json_safe(self.display_score),
            "observation_date": iso_date(self.observation_date),
            "missing": self.missing,
            "missing_reason": self.missing_reason,
            "description": self.description,
            "version": self.version,
        }
