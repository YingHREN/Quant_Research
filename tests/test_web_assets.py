from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web/templates/index.html"
STATIC = ROOT / "web/static"


class WebAssetTest(unittest.TestCase):
    def test_page_has_workstation_regions_and_research_copy(self):
        html = HTML.read_text()
        for marker in (
            'id="universe-panel"',
            'id="price-chart"',
            'id="volume-chart"',
            'id="factor-table"',
            'id="scenario-chart"',
            "Not validated for prediction",
        ):
            self.assertIn(marker, html)

    def test_page_has_no_buy_signal_or_probability_copy(self):
        text = HTML.read_text()
        for banned in ("★ 买点", "上涨概率", "目标价"):
            self.assertNotIn(banned, text)

    def test_chart_library_is_local(self):
        html = HTML.read_text()
        self.assertIn(
            "/static/vendor/lightweight-charts.standalone.production.js", html
        )
        self.assertNotIn("unpkg.com", html)

    def test_vendored_chart_library_is_exactly_pinned(self):
        # lightweight-charts 5.0.8; hashes intentionally pin upstream bytes.
        expected = {
            "lightweight-charts.standalone.production.js": (
                "bcdca2a528db7c9b386918c99c544bde3eda3f0204ec2d23a64411d4cb4686c9"
            ),
            "LICENSE-lightweight-charts.txt": (
                "70c9d5382506dd184465425c08a99ad9bd6d9ac1313c252968ba0b585e5ef823"
            ),
        }
        for name, digest in expected.items():
            payload = (STATIC / "vendor" / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_page_uses_semantic_controls_and_live_status_regions(self):
        html = HTML.read_text()
        for marker in (
            '<button id="update-data"',
            'id="universe-search"',
            'id="sort-key"',
            'role="status"',
            'aria-live="polite"',
        ):
            self.assertIn(marker, html)

    def test_frontend_never_injects_dynamic_html_strings(self):
        scripts = (STATIC / "js").glob("*.js")
        source = "\n".join(path.read_text() for path in scripts)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("insertAdjacentHTML", source)
        self.assertIn("textContent", source)

    def test_universe_helpers_filter_sort_and_preserve_inputs(self):
        module_uri = (STATIC / "js/universe.js").as_uri()
        script = f"""
            import {{ filterTickers, sortTickers }} from {json.dumps(module_uri)};
            const rows = [
              {{ticker: 'MSFT', latest_date: '2026-07-22', lag_days: 0,
                inactive: false, strict_vcp: true, tight_platform: false,
                near_pivot: true, factor_percentile: 92, volatility: 18}},
              {{ticker: 'AAPL', latest_date: '2026-07-20', lag_days: 2,
                inactive: false, strict_vcp: false, tight_platform: true,
                near_pivot: false, factor_percentile: 71, volatility: 24}},
              {{ticker: 'OLD', latest_date: '2025-01-03', lag_days: 565,
                inactive: true, strict_vcp: true, tight_platform: true,
                near_pivot: true, factor_percentile: null, volatility: null}}
            ];
            const snapshot = JSON.stringify(rows);
            const searched = filterTickers(rows, 'ms', {{}}).map(row => row.ticker);
            const filtered = filterTickers(rows, '', {{strictVcp: true, fresh: true}})
              .map(row => row.ticker);
            const inactive = filterTickers(rows, '', {{inactive: true}})
              .map(row => row.ticker);
            const sorted = sortTickers(rows, 'factor_percentile', 'desc')
              .map(row => row.ticker);
            console.log(JSON.stringify({{
              searched, filtered, inactive, sorted,
              unchanged: JSON.stringify(rows) === snapshot
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "searched": ["MSFT"],
                "filtered": ["MSFT"],
                "inactive": ["OLD"],
                "sorted": ["MSFT", "AAPL", "OLD"],
                "unchanged": True,
            },
        )

    def test_initial_ticker_prefers_valid_restore_then_active_row(self):
        module_uri = (STATIC / "js/store.js").as_uri()
        script = f"""
            import {{ chooseInitialTicker }} from {json.dumps(module_uri)};
            const rows = [
              {{ticker: 'OLD', inactive: true}},
              {{ticker: 'AAPL', inactive: false}},
              {{ticker: 'MSFT', inactive: false}}
            ];
            console.log(JSON.stringify([
              chooseInitialTicker(rows, 'msft'),
              chooseInitialTicker(rows, 'MISSING'),
              chooseInitialTicker([{{ticker: 'OLD', inactive: true}}], null),
              chooseInitialTicker([], 'AAPL')
            ]));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(result.stdout), ["MSFT", "AAPL", "OLD", None]
        )


if __name__ == "__main__":
    unittest.main()
