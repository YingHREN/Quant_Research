"""Validated registry for auditable dashboard model outputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass


REGISTRY_VERSION = "model_output_registry_v1"
VALID_CARDINALITIES = frozenset({"many", "single"})
VALID_DECISION_PERMISSIONS = frozenset(
    {
        "informational",
        "advisory",
        "downgrade_to_neutral",
        "veto_to_down",
        "final_policy",
    }
)
_IDENTITY_FIELDS = frozenset(
    {
        "key",
        "group",
        "order",
        "version",
        "kind",
        "lifecycle",
        "timing",
        "decision_permission",
        "name_key",
        "explanation_key",
        "limitation_key",
    }
)


@dataclass(frozen=True)
class ModelOutputGroup:
    key: str
    label_key: str
    order: int
    cardinality: str


@dataclass(frozen=True)
class ModelOutputDefinition:
    key: str
    group: str
    order: int
    version: str | None
    kind: str
    lifecycle: str
    timing: str
    decision_permission: str
    name_key: str
    explanation_key: str
    limitation_key: str


class ModelOutputRegistry:
    """Register presentation metadata and point-in-time output builders."""

    def __init__(self):
        self._groups: dict[str, ModelOutputGroup] = {}
        self._models: dict[
            str,
            tuple[ModelOutputDefinition, Callable[[Mapping], Mapping]],
        ] = {}

    def register_group(self, group: ModelOutputGroup):
        if not isinstance(group, ModelOutputGroup):
            raise TypeError("group must be a ModelOutputGroup")
        if not group.key or not group.label_key:
            raise ValueError("group key and label_key are required")
        if group.cardinality not in VALID_CARDINALITIES:
            raise ValueError("invalid model output group cardinality")
        if group.key in self._groups:
            raise ValueError(f"duplicate model output group: {group.key}")
        self._groups[group.key] = group

    def register_model(
        self,
        definition: ModelOutputDefinition,
        builder: Callable[[Mapping], Mapping],
    ):
        if not isinstance(definition, ModelOutputDefinition):
            raise TypeError(
                "definition must be a ModelOutputDefinition"
            )
        if not callable(builder):
            raise TypeError("builder must be callable")
        if definition.group not in self._groups:
            raise ValueError(
                f"unknown model output group: {definition.group}"
            )
        if (
            definition.decision_permission
            not in VALID_DECISION_PERMISSIONS
        ):
            raise ValueError("invalid model output decision permission")
        if definition.key in self._models:
            raise ValueError(
                f"duplicate model output definition: {definition.key}"
            )
        group = self._groups[definition.group]
        if group.cardinality == "single" and any(
            row.group == definition.group
            for row, _builder in self._models.values()
        ):
            raise ValueError(
                f"single model output group already populated: {group.key}"
            )
        self._models[definition.key] = (definition, builder)

    def public_contract(self):
        groups = sorted(
            self._groups.values(),
            key=lambda row: (row.order, row.key),
        )
        group_order = {row.key: row.order for row in groups}
        models = sorted(
            (definition for definition, _builder in self._models.values()),
            key=lambda row: (
                group_order[row.group],
                row.order,
                row.key,
            ),
        )
        return {
            "version": REGISTRY_VERSION,
            "groups": [asdict(row) for row in groups],
            "models": [asdict(row) for row in models],
        }

    def build(self, context: Mapping):
        if not isinstance(context, Mapping):
            raise TypeError("model output context must be a mapping")
        contract = self.public_contract()
        result = {"registry": contract}
        definitions_by_group = {
            group["key"]: [] for group in contract["groups"]
        }
        for definition, builder in self._models.values():
            definitions_by_group[definition.group].append(
                (definition, builder)
            )
        for group in contract["groups"]:
            entries = []
            definitions = sorted(
                definitions_by_group[group["key"]],
                key=lambda row: (row[0].order, row[0].key),
            )
            for definition, builder in definitions:
                payload = builder(context)
                if not isinstance(payload, Mapping):
                    raise TypeError(
                        f"model output builder returned non-mapping: "
                        f"{definition.key}"
                    )
                conflicts = _IDENTITY_FIELDS.intersection(payload)
                if conflicts:
                    raise ValueError(
                        f"model output builder overrides identity: "
                        f"{definition.key}"
                    )
                entries.append(
                    {
                        **asdict(definition),
                        **dict(payload),
                    }
                )
            result[group["key"]] = (
                entries[0]
                if group["cardinality"] == "single" and entries
                else {}
                if group["cardinality"] == "single"
                else entries
            )
        return result
