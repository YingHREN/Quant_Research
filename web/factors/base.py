"""Common factor protocol and serializable evaluation result."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from web.contracts import iso_date, json_safe
from web.services.analysis import AnalysisContext


class FactorDefinition(Protocol):
    """Contract implemented by dashboard factor plug-ins."""

    key: str
    label: str
    group: str
    direction: str
    description: str
    methodology: str
    overview: bool
    version: str
    window: str | None
    i18n: Mapping[str, Mapping[str, str]]

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
    methodology: str
    overview: bool
    version: str
    window: str | None = None
    i18n: Mapping[str, Mapping[str, str]] | None = None
    percentile: float | None = None
    peer_count: int | None = None
    display_score: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "i18n", freeze_i18n(self.i18n))

    def to_dict(self):
        """Return the stable, JSON-safe factor response shape."""
        result = {
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
            "methodology": self.methodology,
            "overview": self.overview,
            "version": self.version,
        }
        if self.window:
            result["window"] = self.window
        if self.i18n:
            result["i18n"] = json_safe(
                {locale: dict(fields) for locale, fields in self.i18n.items()}
            )
        return result


@dataclass(frozen=True)
class FactorGroup:
    """Registry metadata for one user-facing factor group."""

    key: str
    label: str
    methodology: str
    overview: bool
    i18n: Mapping[str, Mapping[str, str]] | None = None

    def __post_init__(self):
        object.__setattr__(self, "i18n", freeze_i18n(self.i18n))

    def to_dict(self):
        result = {
            "key": self.key,
            "label": self.label,
            "methodology": self.methodology,
            "overview": self.overview,
        }
        if self.i18n:
            result["i18n"] = json_safe(
                {locale: dict(fields) for locale, fields in self.i18n.items()}
            )
        return result


def freeze_i18n(value):
    """Copy localization metadata into immutable nested mappings."""
    if not value:
        return MappingProxyType({})
    return MappingProxyType(
        {
            str(locale): MappingProxyType(
                {str(field): str(text) for field, text in fields.items()}
            )
            for locale, fields in value.items()
        }
    )
