from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class IntradayLiveAssetTest(unittest.TestCase):
    def test_dashboard_exposes_subscription_and_live_panel_regions(self):
        template = (ROOT / "web/templates/index.html").read_text()
        for element_id in (
            "intraday-subscription-summary",
            "intraday-subscription-list",
            "selected-realtime-toggle",
            "intraday-live-panel",
            "intraday-live-state",
            "intraday-last-trade",
            "intraday-bid",
            "intraday-ask",
            "intraday-spread",
            "intraday-price-chart",
            "intraday-volume-chart",
            "intraday-pressure-bar",
            "intraday-pressure-detail",
        ):
            self.assertIn(f'id="{element_id}"', template)

    def test_api_and_controller_are_syntax_valid_and_do_not_use_html_injection(self):
        api = (ROOT / "web/static/js/api.js").read_text()
        controller = (ROOT / "web/static/js/intraday-live.js").read_text()
        self.assertIn("/api/market-data/subscriptions", api)
        self.assertIn("/api/intraday/", api)
        self.assertIn("visibilitychange", controller)
        self.assertIn("2000", controller)
        self.assertNotIn("innerHTML", controller)
        for path in (
            ROOT / "web/static/js/api.js",
            ROOT / "web/static/js/intraday-live.js",
            ROOT / "web/static/js/universe.js",
            ROOT / "web/static/js/app.js",
        ):
            subprocess.run(
                ["node", "--check", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_intraday_copy_is_present_in_both_locales(self):
        source = (ROOT / "web/static/js/i18n.js").read_text()
        for key in (
            "intraday.title",
            "intraday.subscription.add",
            "intraday.subscription.remove",
            "intraday.state.live",
            "intraday.state.pending",
            "intraday.pressure",
            "intraday.iexLimitation",
        ):
            self.assertGreaterEqual(source.count(f'"{key}"'), 2)


if __name__ == "__main__":
    unittest.main()
