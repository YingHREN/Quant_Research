import json
import unittest

from web.forecasts.model_output_registry import (
    ModelOutputDefinition,
    ModelOutputGroup,
    ModelOutputRegistry,
)


def definition(
    key,
    group,
    *,
    order=10,
    permission="advisory",
):
    return ModelOutputDefinition(
        key=key,
        group=group,
        order=order,
        version="v1",
        kind="rule_score",
        lifecycle="production",
        timing="close_confirmed",
        decision_permission=permission,
        name_key=f"model.{key}.name",
        explanation_key=f"model.{key}.explanation",
        limitation_key=f"model.{key}.limitation",
    )


class ModelOutputRegistryTest(unittest.TestCase):
    def test_build_sorts_groups_and_models_and_exposes_public_contract(self):
        registry = ModelOutputRegistry()
        registry.register_group(
            ModelOutputGroup(
                "risk",
                "group.risk",
                20,
                "many",
            )
        )
        registry.register_group(
            ModelOutputGroup(
                "primary",
                "group.primary",
                10,
                "many",
            )
        )
        registry.register_model(
            definition("later", "risk", order=20),
            lambda context: {
                "status": "active",
                "score": context["score"],
            },
        )
        registry.register_model(
            definition("earlier", "risk", order=10),
            lambda context: {"status": "inactive", "score": 7},
        )

        outputs = registry.build({"score": 42})

        self.assertEqual(
            [row["key"] for row in outputs["registry"]["groups"]],
            ["primary", "risk"],
        )
        self.assertEqual(
            [row["key"] for row in outputs["risk"]],
            ["earlier", "later"],
        )
        self.assertEqual(outputs["risk"][1]["score"], 42)
        self.assertEqual(outputs["risk"][1]["group"], "risk")
        self.assertEqual(outputs["risk"][1]["order"], 20)
        self.assertEqual(
            outputs["risk"][1]["decision_permission"],
            "advisory",
        )
        self.assertEqual(
            outputs["registry"]["version"],
            "model_output_registry_v1",
        )
        json.dumps(outputs)

    def test_single_group_emits_one_object(self):
        registry = ModelOutputRegistry()
        registry.register_group(
            ModelOutputGroup(
                "decision",
                "group.decision",
                40,
                "single",
            )
        )
        registry.register_model(
            definition(
                "policy",
                "decision",
                permission="final_policy",
            ),
            lambda context: {
                "status": "available",
                "final_direction": "down",
            },
        )

        outputs = registry.build({})

        self.assertEqual(outputs["decision"]["key"], "policy")
        self.assertEqual(outputs["decision"]["final_direction"], "down")

    def test_explicit_version_resolver_preserves_runtime_model_version(self):
        registry = ModelOutputRegistry()
        registry.register_group(
            ModelOutputGroup(
                "primary",
                "group.primary",
                10,
                "many",
            )
        )
        registry.register_model(
            definition("ridge", "primary"),
            lambda context: {"status": "available"},
            version_resolver=lambda context: context["model_version"],
        )

        outputs = registry.build({"model_version": "v4"})

        self.assertEqual(outputs["primary"][0]["version"], "v4")
        self.assertEqual(
            outputs["registry"]["models"][0]["version"],
            "v1",
        )

    def test_registration_rejects_invalid_or_ambiguous_definitions(self):
        registry = ModelOutputRegistry()
        registry.register_group(
            ModelOutputGroup("risk", "group.risk", 20, "many")
        )

        cases = (
            (
                "duplicate group",
                lambda: registry.register_group(
                    ModelOutputGroup("risk", "group.other", 30, "many")
                ),
                ValueError,
            ),
            (
                "invalid cardinality",
                lambda: registry.register_group(
                    ModelOutputGroup("bad", "group.bad", 30, "optional")
                ),
                ValueError,
            ),
            (
                "unknown group",
                lambda: registry.register_model(
                    definition("orphan", "missing"),
                    lambda context: {},
                ),
                ValueError,
            ),
            (
                "invalid decision permission",
                lambda: registry.register_model(
                    definition(
                        "bad_permission",
                        "risk",
                        permission="always_override",
                    ),
                    lambda context: {},
                ),
                ValueError,
            ),
        )
        for label, operation, error_type in cases:
            with self.subTest(label=label):
                with self.assertRaises(error_type):
                    operation()

        registry.register_model(
            definition("risk_one", "risk"),
            lambda context: {"status": "active"},
        )
        with self.assertRaises(ValueError):
            registry.register_model(
                definition("risk_one", "risk"),
                lambda context: {"status": "inactive"},
            )

    def test_build_rejects_non_mapping_and_identity_overrides(self):
        for key, builder, error_type in (
            ("not_mapping", lambda context: None, TypeError),
            (
                "identity_override",
                lambda context: {
                    "key": "different",
                    "status": "active",
                },
                ValueError,
            ),
        ):
            with self.subTest(key=key):
                registry = ModelOutputRegistry()
                registry.register_group(
                    ModelOutputGroup(
                        "risk",
                        "group.risk",
                        20,
                        "many",
                    )
                )
                registry.register_model(
                    definition(key, "risk"),
                    builder,
                )
                with self.assertRaises(error_type):
                    registry.build({})


if __name__ == "__main__":
    unittest.main()
