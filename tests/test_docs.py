from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_GUIDE = ROOT / "docs/dashboard.md"
RESEARCH_DECISION = ROOT / "docs/research/vcp-integration-decision-v1.md"


def normalized_text(path):
    return " ".join(path.read_text(encoding="utf-8").split())


class DocumentationContractTest(unittest.TestCase):
    def test_dashboard_guide_documents_localized_factor_extension(self):
        guide = normalized_text(DASHBOARD_GUIDE)

        for marker in (
            "A complete Simplified Chinese entry has all five fields: `label`, `description`, `methodology`, `window`, and `direction`.",
            "i18n={\"zh-CN\": {...}}",
            "FACTOR_ZH",
            "GROUP_ZH",
            "tests/test_web_factors.py",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, guide)

    def test_dashboard_guide_documents_forecast_policy_and_cache_lifecycle(self):
        guide = normalized_text(DASHBOARD_GUIDE)

        for marker in (
            "SUPPORTED_HORIZONS = (5, 20, 60)",
            "NEUTRAL_BANDS = {5: 0.01, 20: 0.02, 60: 0.04}",
            "ForecastRegistry.register(provider)",
            "FORECAST_SERVICE",
            "(database_revision, ticker, first_chart_date, last_chart_date, model_version)",
            "insufficient_calibration_samples",
            "calibration_requires_both_classes",
            "positive-return event (`actual_return > 0`)",
            "partial or rate-limited job after one or more commits can leave stale forecasts",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, guide)

    def test_research_decision_documents_reproducible_leakage_safe_evaluation(self):
        decision = normalized_text(RESEARCH_DECISION)

        for marker in (
            "attach_forward_targets(build_feature_frame(histories))",
            "walk_forward_evaluate(frame, horizon, provider)",
            "eligible only when its `label_end_date` is strictly before `t`",
            "MAE and RMSE measure return-prediction error",
            "zero-return baseline and expanding historical-mean baseline",
            "`direction accuracy` is the share of the three fixed-band classes",
            "`rank IC` is the mean per-date Spearman correlation",
            "`signal-bucket returns` are realized mean returns",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, decision)


if __name__ == "__main__":
    unittest.main()
