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
            "macro-risk",
            "sector-drilldown",
            "market-events",
            "market-data-tier",
            "market-coverage",
            "macro-history-score-chart",
            "macro-history-context-chart",
            "macro-history-detail",
            "macro-history-series",
            "macro-history-unlock",
        ):
            self.assertIn(f'id="{element_id}"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn('href="/"', source)
        self.assertIn('data-horizon="5"', source)
        self.assertIn('data-horizon="20"', source)
        self.assertIn('data-horizon="60"', source)
        self.assertLess(
            source.index("lightweight-charts.standalone.production.js"),
            source.index('src="/static/js/market.js"'),
        )

    def test_macro_history_selection_stays_locked_until_explicit_unlock(self):
        module = (
            ROOT / "web/static/js/macro-history-chart.mjs"
        ).resolve()
        script = f"""
          import {{
            benchmarkChartValue,
            chartSeriesData,
            clearChartCrosshairs,
            createSelectionState,
          }} from {module.as_uri()!r};
          if (benchmarkChartValue({{
            benchmark_close: 612.34,
            benchmark_normalized: 129.12,
          }}) !== 612.34) process.exit(6);
          let cleared = 0;
          clearChartCrosshairs([
            {{ clearCrosshairPosition: () => cleared += 1 }},
            {{ clearCrosshairPosition: () => cleared += 1 }},
          ]);
          if (cleared !== 2) process.exit(7);
          const gaps = chartSeriesData([
            {{ time: "2026-06-30", value: null }},
            {{ time: "2026-07-01", value: 25 }},
          ], row => row.value);
          if (Object.hasOwn(gaps[0], "value")) process.exit(1);
          if (gaps[1].value !== 25) process.exit(5);
          const selection = createSelectionState([
            {{ time: "2026-07-01" }},
            {{ time: "2026-07-02" }},
          ]);
          selection.hover("2026-07-01");
          selection.toggleLock("2026-07-01");
          selection.hover("2026-07-02");
          if (selection.selected().time !== "2026-07-01") process.exit(2);
          if (!selection.selected().locked) process.exit(3);
          selection.replaceRows([
            {{ time: "2026-07-01" }},
            {{ time: "2026-07-02" }},
            {{ time: "2026-07-03" }},
          ]);
          selection.reset();
          if (selection.selected().locked) process.exit(8);
          if (selection.selected().time !== "2026-07-03") process.exit(9);
          selection.unlock();
          selection.hover("2026-07-02");
          if (selection.selected().time !== "2026-07-02") process.exit(4);
        """

        subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_macro_history_chart_uses_actual_benchmark_and_clears_selection(self):
        source = (
            ROOT / "web/static/js/macro-history-chart.mjs"
        ).read_text()
        market_source = (ROOT / "web/static/js/market.js").read_text()

        self.assertIn(
            "chartSeriesData(rows, benchmarkChartValue)",
            source,
        )
        self.assertIn(
            "clearChartCrosshairs([scoreChart, contextChart])",
            source,
        )
        self.assertIn("selection.reset();", source)
        self.assertIn("timeFormatter: formatCrosshairDate", source)
        self.assertGreaterEqual(
            market_source.count("macroHistoryCharts?.resetSelection();"),
            2,
        )

    def test_market_js_uses_payload_evidence_without_recomputing_scores(self):
        source = (ROOT / "web/static/js/market.js").read_text()
        self.assertIn("payload.market_posture", source)
        self.assertIn("payload.macro_risk", source)
        self.assertIn("payload.selected_group", source)
        self.assertIn("payload.theme_groups", source)
        self.assertIn('setAttribute("aria-pressed"', source)
        self.assertIn("row.relative_return", source)
        self.assertIn("row.downside_risk", source)
        self.assertIn("function riskDisplayScore(", source)
        self.assertIn("risk.state_score", source)
        self.assertIn("risk.raw_score", source)
        self.assertIn("risk.memory_age_sessions", source)
        self.assertNotIn("reversalOpportunityScore(", source)
        self.assertNotIn("downsideRiskScore(", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("const semiconductor = {", source)
        self.assertNotIn("const software = {", source)

    def test_locale_change_renders_current_request_status(self):
        source = (ROOT / "web/static/js/market.js").read_text()

        self.assertIn('status: { kind: "idle", error: null }', source)
        self.assertIn("function renderStatus()", source)
        self.assertIn("renderStatus();", source)
        self.assertNotIn('if (state.payload) {\n    render(state.payload);\n    setStatus("");', source)

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
            "market.riskState.new",
            "market.riskState.persistent",
            "market.riskState.fading",
            "market.riskState.inactive",
            "market.risk.detail",
            "market.risk.modelSource",
            "market.unavailable.missing_sector_benchmark",
            "market.evidence.failed_breakout",
            "market.evidence.help.failed_breakout",
            "market.group.semiconductor",
            "market.group.software",
            "market.sector.technology",
            "market.sector.utilities",
            "market.macro.history.title",
            "market.macro.history.locked",
            "market.macro.history.unlocked",
            "market.macro.history.series.CPI_YOY",
            "market.macro.evidence.two_year_yield_high",
        )
        for key in required:
            with self.subTest(key=key):
                self.assertGreaterEqual(source.count(f'"{key}"'), 2)

    def test_directional_signal_names_and_model_sources_are_bilingual(self):
        source = (ROOT / "web/static/js/i18n.js").read_text()
        for copy in (
            "向上结构反转候选 {count}/3",
            "三条件价格结构规则模型",
            "向上早期反转观察",
            "四条件规则评分模型",
            "向下转折风险",
            "12项市场/板块/个股规则评分模型",
            "向上突破准备形态",
            "VCP数学形态规则",
            "Bullish structural reversal candidate {count}/3",
            "Three-condition price-structure rule model",
            "Early bullish reversal watch",
            "Four-condition rule-scoring model",
            "Bearish turn risk",
            "12-rule market/sector/stock scoring model",
            "Bullish breakout setup",
            "VCP mathematical shape rules",
        ):
            with self.subTest(copy=copy):
                self.assertIn(copy, source)

    def test_stock_dashboard_links_to_market_page(self):
        source = (ROOT / "web/templates/index.html").read_text()
        self.assertIn('href="/market"', source)
        self.assertIn('data-i18n="market.nav.market"', source)

    def test_market_javascript_is_valid(self):
        for path in (
            ROOT / "web/static/js/api.js",
            ROOT / "web/static/js/i18n.js",
            ROOT / "web/static/js/market.js",
            ROOT / "web/static/js/macro-history-chart.mjs",
        ):
            subprocess.run(
                ["node", "--check", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
