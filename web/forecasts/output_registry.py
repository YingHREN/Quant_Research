"""Typed registry for independently rendered forecast model outputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


MODEL_OUTPUT_FAMILIES = (
    "primary",
    "downside",
    "bullish_structure",
)


@dataclass(frozen=True)
class ModelOutputContext:
    forecast: Mapping
    chart_row: Mapping
    evaluation: Mapping
    decision: Mapping

    def __post_init__(self):
        for field_name in (
            "forecast",
            "chart_row",
            "evaluation",
            "decision",
        ):
            if not isinstance(getattr(self, field_name), Mapping):
                raise TypeError(f"{field_name} must be a mapping")


@dataclass(frozen=True)
class ModelOutputRegistration:
    key: str
    family: str
    order: int
    builder: Callable[[ModelOutputContext], Mapping]

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("key must be a non-empty string")
        if self.family not in MODEL_OUTPUT_FAMILIES:
            raise ValueError("family is not a supported model output family")
        if isinstance(self.order, bool) or not isinstance(self.order, int):
            raise TypeError("order must be an integer")
        if not callable(self.builder):
            raise TypeError("builder must be callable")


class ModelOutputRegistry:
    """Immutable ordered registrations with per-builder fault isolation."""

    def __init__(self, registrations=()):
        registrations = tuple(registrations)
        keys = [registration.key for registration in registrations]
        if len(keys) != len(set(keys)):
            raise ValueError("model output registration keys must be unique")
        if not all(
            isinstance(registration, ModelOutputRegistration)
            for registration in registrations
        ):
            raise TypeError(
                "registrations must contain ModelOutputRegistration values"
            )
        self._registrations = tuple(
            sorted(
                registrations,
                key=lambda item: (
                    MODEL_OUTPUT_FAMILIES.index(item.family),
                    item.order,
                    item.key,
                ),
            )
        )

    @property
    def registrations(self):
        return self._registrations

    def register(self, registration):
        """Return a new registry containing one additional model output."""
        if not isinstance(registration, ModelOutputRegistration):
            raise TypeError("registration must be a ModelOutputRegistration")
        return type(self)((*self._registrations, registration))

    def build(self, context):
        if not isinstance(context, ModelOutputContext):
            raise TypeError("context must be a ModelOutputContext")
        grouped = {family: [] for family in MODEL_OUTPUT_FAMILIES}
        for registration in self._registrations:
            try:
                output = dict(registration.builder(context))
            except Exception:
                output = _failed_output(registration)
            grouped[registration.family].append(output)
        return grouped


def _failed_output(registration):
    return {
        "key": registration.key,
        "version": None,
        "kind": "registry_error",
        "lifecycle": "production",
        "status": "unavailable",
        "timing": "close_confirmed",
        "name_key": None,
        "explanation_key": None,
        "limitation_key": None,
        "unavailable_reason": "builder_failed",
    }
