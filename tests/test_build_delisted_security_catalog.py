from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from build_delisted_security_catalog import build_catalog_files


class BuildDelistedSecurityCatalogTest(unittest.TestCase):
    def test_builds_stable_catalog_and_audited_reports_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "Code": "AAA",
                            "Name": "AAA Inc",
                            "Exchange": "NASDAQ",
                            "Currency": "USD",
                            "Type": "Common Stock",
                            "Isin": "US0378331005",
                        },
                        {
                            "Code": "BBB-WS",
                            "Name": "BBB Warrants",
                            "Exchange": "NYSE",
                            "Currency": "USD",
                            "Type": "Common Stock",
                            "Isin": None,
                        },
                        {
                            "Code": "CCC",
                            "Name": "CCC",
                            "Exchange": "NYSE MKT",
                            "Currency": "USD",
                            "Type": "Common Stock",
                            "Isin": None,
                        },
                        {
                            "Code": "DDD",
                            "Name": "DDD Inc",
                            "Exchange": "PINK",
                            "Currency": "USD",
                            "Type": "Common Stock",
                            "Isin": None,
                        },
                    ]
                )
            )
            output = root / "cache" / "catalog.json"
            manifest = root / "cache" / "manifest.json"
            report_json = root / "reports" / "report.json"
            report_markdown = root / "reports" / "report.md"

            with patch.dict(
                os.environ,
                {"EODHD_API_TOKEN": "must-never-be-persisted"},
            ):
                first = build_catalog_files(
                    source,
                    output_catalog=output,
                    manifest_path=manifest,
                    report_json=report_json,
                    report_markdown=report_markdown,
                    observed_at="2026-07-27T12:00:00Z",
                )
                stable_catalog = output.read_bytes()
                second = build_catalog_files(
                    source,
                    output_catalog=output,
                    manifest_path=manifest,
                    report_json=report_json,
                    report_markdown=report_markdown,
                    observed_at="2026-07-27T13:00:00Z",
                )

            self.assertEqual(first["summary"], second["summary"])
            self.assertEqual(stable_catalog, output.read_bytes())
            self.assertEqual(first["input_sha256"], second["input_sha256"])
            self.assertEqual(first["catalog_sha256"], second["catalog_sha256"])
            self.assertEqual(first["summary"]["input_rows"], 4)
            self.assertEqual(
                first["summary"]["classification_counts"],
                {
                    "accepted_common": 1,
                    "needs_review": 1,
                    "out_of_scope": 1,
                    "rejected_non_common": 1,
                },
            )
            manifest_payload = json.loads(manifest.read_text())
            self.assertEqual(
                manifest_payload["observed_at"],
                "2026-07-27T13:00:00Z",
            )
            self.assertEqual(
                manifest_payload["catalog_sha256"],
                first["catalog_sha256"],
            )
            report = json.loads(report_json.read_text())
            self.assertNotIn("securities", report)
            self.assertIn("证券类型净化", report_markdown.read_text())
            self.assertIn(
                "不是指数成员区间",
                report_markdown.read_text(),
            )
            persisted = (
                output.read_text()
                + manifest.read_text()
                + report_json.read_text()
                + report_markdown.read_text()
            )
            self.assertNotIn("must-never-be-persisted", persisted)
            self.assertFalse(list(root.rglob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
