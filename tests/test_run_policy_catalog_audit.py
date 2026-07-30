import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research.run_policy_catalog_audit import (
    build_policy_catalog_audit,
    write_policy_catalog_audit,
)


def history(values):
    return pd.DataFrame(
        {"Close": values},
        index=pd.to_datetime(
            (
                "2020-01-02",
                "2020-01-31",
                "2020-02-28",
                "2020-03-31",
            )
        ),
    )


def periods():
    return pd.DataFrame(
        [
            {
                "period_id": "complete",
                "catalog_version": "fed-policy-v1",
                "label_zh": "完整时期",
                "label_en": "Complete",
                "start_date": "2020-01-02",
                "end_date": "2020-03-31",
                "available_at": "2020-03-31T20:00:00+00:00",
            },
            {
                "period_id": "open",
                "catalog_version": "fed-policy-v1",
                "label_zh": "进行中",
                "label_en": "Ongoing",
                "start_date": "2020-04-01",
                "end_date": None,
                "available_at": "2020-04-01T20:00:00+00:00",
            },
        ]
    )


class RunPolicyCatalogAuditTest(unittest.TestCase):
    def test_report_keeps_missing_not_listed_and_incomplete_rows(self):
        histories = {
            "SPY": history([100.0, 102.0, 105.0, 110.0]),
            "XLRE": pd.DataFrame(
                {"Close": [10.0, 11.0]},
                index=pd.to_datetime(("2020-04-01", "2020-04-02")),
            ),
        }

        payload = build_policy_catalog_audit(
            events=pd.DataFrame(
                [
                    {
                        "event_id": "event-1",
                        "event_type": "policy_rate",
                    }
                ]
            ),
            periods=periods(),
            histories=histories,
            asof="2020-04-03T00:00:00+00:00",
            catalog_version="fed-policy-v1",
        )
        statuses = {
            row["status"]
            for row in payload["period_results"]
        }

        self.assertIn("missing_history", statuses)
        self.assertIn("not_listed", statuses)
        self.assertIn("incomplete", statuses)
        self.assertEqual(
            len(payload["period_results"]),
            2 * 13,
        )

    def test_report_is_descriptive_and_has_no_model_score(self):
        payload = build_policy_catalog_audit(
            events=pd.DataFrame(
                columns=["event_id", "event_type"]
            ),
            periods=periods(),
            histories={"SPY": history([100, 101, 102, 103])},
            asof="2020-04-03T00:00:00+00:00",
            catalog_version="fed-policy-v1",
        )
        serialized = json.dumps(payload, allow_nan=False)

        self.assertEqual(
            payload["report_type"],
            "descriptive_policy_audit",
        )
        self.assertEqual(payload["lifecycle"], "research")
        self.assertEqual(payload["decision_permission"], "advisory")
        self.assertEqual(payload["online_authority"], "none")
        self.assertNotIn('"model_score"', serialized)
        self.assertNotIn('"probability"', serialized)
        self.assertNotIn('"recommendation"', serialized)

    def test_writer_emits_strict_json_and_omits_local_paths(self):
        payload = build_policy_catalog_audit(
            events=pd.DataFrame(
                columns=["event_id", "event_type"]
            ),
            periods=periods(),
            histories={"SPY": history([100, 101, 102, 103])},
            asof="2020-04-03T00:00:00+00:00",
            catalog_version="fed-policy-v1",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "audit.json"
            markdown_path = root / "audit.md"

            write_policy_catalog_audit(
                payload,
                json_path=json_path,
                markdown_path=markdown_path,
            )
            decoded = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

            self.assertEqual(decoded["task_key"], "MACRO-ROTATION-001")
            self.assertNotIn(str(root), json.dumps(decoded))
            self.assertNotIn(str(root), markdown)


if __name__ == "__main__":
    unittest.main()
