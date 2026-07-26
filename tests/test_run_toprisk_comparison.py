import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

import run_toprisk_comparison


class RunTopRiskComparisonTest(unittest.TestCase):
    def test_cli_writes_deterministic_json_and_markdown_with_limitations(self):
        frame = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "observation_date": [pd.Timestamp("2026-07-01")],
                "close": [100.0],
                "low": [100.0],
                "signal_ridge_down": [pd.NA],
                "signal_immediate_8": [False],
                "signal_memory_12": [False],
                "signal_toprisk_confirmed": [False],
                "signal_toprisk_stateful": [False],
                "signal_ridge_plus_toprisk": [pd.NA],
            }
        ).set_index(["ticker", "observation_date"])
        fake_rows = [
            {
                "group": "all",
                "horizon_sessions": 5,
                "signal": "ridge_down",
                "status": "unavailable",
                "sample_count": 0,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_json = Path(directory) / "comparison.json"
            output_markdown = Path(directory) / "comparison.md"
            with mock.patch.object(
                run_toprisk_comparison,
                "_load_histories",
                return_value={"AAA": pd.DataFrame()},
            ), mock.patch.object(
                run_toprisk_comparison,
                "build_comparison_frame",
                return_value=frame,
            ), mock.patch.object(
                run_toprisk_comparison,
                "evaluate_signals",
                return_value=fake_rows,
            ):
                code = run_toprisk_comparison.main(
                    [
                        "--database",
                        "ignored.db",
                        "--output-json",
                        str(output_json),
                        "--output-markdown",
                        str(output_markdown),
                    ]
                )
            payload = json.loads(output_json.read_text())
            markdown = output_markdown.read_text()

        self.assertEqual(code, 0)
        self.assertEqual(payload["model_versions"]["toprisk"], "v1")
        self.assertEqual(payload["market_regimes"]["status"], "unavailable")
        self.assertIn("Ridge historical forecasts are unavailable", markdown)
        self.assertIn("| Group | Horizon | Signal |", markdown)


if __name__ == "__main__":
    unittest.main()
