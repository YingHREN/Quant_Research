from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MarketAssetTest(unittest.TestCase):
    def test_market_template_has_accessible_command_center_regions(self):
        source = (ROOT / "web/templates/market.html").read_text()
        for element_id in (
            "market-posture",
            "sector-heatmap",
            "market-evidence",
            "sector-drilldown",
            "market-events",
            "market-data-tier",
            "market-coverage",
        ):
            self.assertIn(f'id="{element_id}"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn('href="/"', source)
        self.assertIn('data-horizon="5"', source)
        self.assertIn('data-horizon="20"', source)
        self.assertIn('data-horizon="60"', source)

    def test_market_js_uses_payload_evidence_without_recomputing_scores(self):
        source = (ROOT / "web/static/js/market.js").read_text()
        self.assertIn("payload.market_posture", source)
        self.assertIn("payload.selected_group", source)
        self.assertIn('setAttribute("aria-pressed"', source)
        self.assertIn("row.relative_return", source)
        self.assertIn("row.downside_risk", source)
        self.assertNotIn("reversalOpportunityScore(", source)
        self.assertNotIn("downsideRiskScore(", source)
        self.assertNotIn("innerHTML", source)

    def test_market_keys_are_present_in_both_locales(self):
        source = (ROOT / "web/static/js/i18n.js").read_text()
        required = (
            "market.document.title",
            "market.nav.stock",
            "market.nav.market",
            "market.title",
            "market.posture.title",
            "market.sectors.title",
            "market.evidence.title",
            "market.drilldown.title",
            "market.events.title",
            "market.tier.daily_proxy",
            "market.state.met",
            "market.state.near",
            "market.state.unmet",
            "market.state.unavailable",
            "market.unavailable.missing_sector_benchmark",
            "market.evidence.failed_breakout",
            "market.evidence.help.failed_breakout",
            "market.group.semiconductor",
            "market.sector.technology",
            "market.sector.utilities",
        )
        for key in required:
            with self.subTest(key=key):
                self.assertGreaterEqual(source.count(f'"{key}"'), 2)

    def test_stock_dashboard_links_to_market_page(self):
        source = (ROOT / "web/templates/index.html").read_text()
        self.assertIn('href="/market"', source)
        self.assertIn('data-i18n="market.nav.market"', source)

    def test_market_javascript_is_valid(self):
        for path in (
            ROOT / "web/static/js/api.js",
            ROOT / "web/static/js/i18n.js",
            ROOT / "web/static/js/market.js",
        ):
            subprocess.run(
                ["node", "--check", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
