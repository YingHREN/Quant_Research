from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
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
            "NOTICE-lightweight-charts.txt": (
                "f76c6afab94884448f0426e30d6e9d555ca7247894cd3484e477d2f87513036e"
            ),
        }
        for name, digest in expected.items():
            payload = (STATIC / "vendor" / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_page_carries_visible_tradingview_attribution(self):
        html = HTML.read_text()
        self.assertIn("Charts by TradingView", html)
        self.assertIn('href="https://www.tradingview.com/"', html)

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
                inactive: false, stale: false, strict_vcp: true, tight_platform: false,
                near_pivot: true, momentum_percentile: 92, volatility: 18}},
              {{ticker: 'AAPL', latest_date: '2026-07-20', lag_days: 2,
                inactive: false, stale: true, strict_vcp: false, tight_platform: true,
                near_pivot: false, momentum_percentile: 71, volatility: 24}},
              {{ticker: 'OLD', latest_date: '2025-01-03', lag_days: 565,
                inactive: true, stale: false, strict_vcp: true, tight_platform: true,
                near_pivot: true, momentum_percentile: null, volatility: null}}
            ];
            const snapshot = JSON.stringify(rows);
            const searched = filterTickers(rows, 'ms', {{}}).map(row => row.ticker);
            const filtered = filterTickers(rows, '', {{strictVcp: true, fresh: true}})
              .map(row => row.ticker);
            const inactive = filterTickers(rows, '', {{inactive: true}})
              .map(row => row.ticker);
            const sorted = sortTickers(rows, 'momentum_percentile', 'desc')
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
                "inactive": ["AAPL", "OLD"],
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

    def test_i18n_module(self):
        module_uri = (STATIC / "js/i18n.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            const values = new Map();
            const storage = {{
              getItem(key) {{ return values.has(key) ? values.get(key) : null; }},
              setItem(key, value) {{ values.set(key, String(value)); }},
            }};
            globalThis.localStorage = storage;
            const i18n = await import({json.dumps(module_uri)});
            assert.equal(i18n.getLocale(), "zh-CN");
            const notifications = [];
            const unsubscribe = i18n.subscribeLocale((locale) => notifications.push(locale));
            assert.equal(i18n.setLocale("en"), "en");
            assert.deepEqual(notifications, ["en"]);
            assert.equal(storage.getItem("quant-dashboard-locale"), "en");
            unsubscribe();
            assert.equal(i18n.setLocale("fr"), "zh-CN");
            assert.deepEqual(notifications, ["en"]);
            assert.equal(
              i18n.t("debug.englishFallback", {{}}, "zh-CN"),
              "English fallback",
            );
            assert.equal(i18n.formatChartTickDate("2026-07-17"), "07-17");
            assert.equal(
              i18n.formatFullDate({{ year: 2026, month: 7, day: 17 }}),
              "2026-07-17",
            );
            assert.equal(
              i18n.t("universe.shown", {{ shown: 2, total: 3 }}, "zh-CN"),
              "显示 2/3 只股票",
            );
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_bilingual_dashboard(self):
        html = HTML.read_text()
        self.assertEqual(len(re.findall(r'data-locale="(?:zh-CN|en)"', html)), 2)
        self.assertIn('data-locale="zh-CN" aria-pressed="true"', html)
        self.assertIn('data-locale="en" aria-pressed="false"', html)
        for key in (
            "header.latestDate",
            "universe.filters.strictVcp",
            "security.state.stale",
            "chart.range.3m",
            "factor.title",
            "scenario.disclaimer",
            "update.state.rateLimited",
        ):
            self.assertIn(key, (STATIC / "js/i18n.js").read_text())
        for marker in (
            'data-i18n="header.latestDate"',
            'data-i18n="universe.filters.strictVcp"',
            'data-i18n="factor.title"',
            'data-i18n="scenario.disclaimer"',
            'data-i18n-aria-label="chart.priceAria"',
            'data-i18n-aria-label="chart.volumeAria"',
        ):
            self.assertIn(marker, html)
        css = (STATIC / "css/dashboard.css").read_text()
        for marker in (
            ".locale-control",
            '.locale-control button[aria-pressed="true"]',
            "button:focus-visible",
            "@media (max-width: 390px)",
        ):
            self.assertIn(marker, css)

        module_uri = (STATIC / "js/i18n.js").as_uri()
        update_uri = (STATIC / "js/update.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            const i18n = await import({json.dumps(module_uri)});
            const update = await import({json.dumps(update_uri)});

            function element(dataset = {{}}) {{
              return {{dataset: {{...dataset}}, textContent: '', attributes: {{}},
                setAttribute(name, value) {{ this.attributes[name] = String(value); }},
                getAttribute(name) {{ return this.attributes[name]; }}}};
            }}
            const title = element({{i18n: 'factor.title'}});
            const range = element({{i18n: 'chart.range.3m'}});
            const priceChart = element({{i18nAriaLabel: 'chart.priceAria'}});
            const zhButton = element({{locale: 'zh-CN'}});
            const enButton = element({{locale: 'en'}});
            const selectors = new Map([
              ['[data-i18n]', [title, range]],
              ['[data-i18n-placeholder]', []],
              ['[data-i18n-aria-label]', [priceChart]],
              ['[data-locale]', [zhButton, enButton]],
            ]);
            const root = {{documentElement: {{lang: 'en'}},
              querySelectorAll(selector) {{ return selectors.get(selector) || []; }}}};

            i18n.applyDocumentLocale(root, 'zh-CN');
            assert.equal(root.documentElement.lang, 'zh-CN');
            assert.equal(title.textContent, '因子概览');
            assert.equal(range.textContent, '3个月');
            assert.equal(priceChart.getAttribute('aria-label'), 'K线价格图');
            assert.equal(zhButton.getAttribute('aria-pressed'), 'true');
            assert.equal(enButton.getAttribute('aria-pressed'), 'false');

            const button = {{disabled: false, textContent: '', dataset: {{}},
              addEventListener() {{}}, removeEventListener() {{}}}};
            const status = {{textContent: '', dataset: {{}}}};
            const controller = update.createUpdateController({{button, status,
              apiClient: {{}}, schedule() {{}}, cancel() {{}}}});
            assert.equal(status.textContent, '仅价格更新状态：空闲');

            i18n.applyDocumentLocale(root, i18n.setLocale('en'));
            assert.equal(root.documentElement.lang, 'en');
            assert.equal(title.textContent, 'Factor overview');
            assert.equal(range.textContent, '3M');
            assert.equal(priceChart.getAttribute('aria-label'), 'Candlestick price chart');
            assert.equal(zhButton.getAttribute('aria-pressed'), 'false');
            assert.equal(enButton.getAttribute('aria-pressed'), 'true');
            assert.equal(status.textContent, 'Price-only update status: idle');
            controller.destroy();
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_daily_return_formatting_uses_fraction_units_and_quote_clear_is_explicit(self):
        module_uri = (STATIC / "js/app.js").as_uri()
        script = f"""
            import * as appModule from {json.dumps(module_uri)};
            const fields = {{
              selectedClose: {{textContent: '101.25'}},
              selectedChange: {{textContent: '+1.00%'}},
              observationDate: {{textContent: '2026-07-22'}},
            }};
            if (appModule.clearStockQuote) appModule.clearStockQuote(fields);
            console.log(JSON.stringify({{
              changes: appModule.formatDailyReturn
                ? [0.01, -0.025, 0].map((value) => appModule.formatDailyReturn(value)) : null,
              quote: [fields.selectedClose.textContent, fields.selectedChange.textContent,
                      fields.observationDate.textContent],
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
            {"changes": ["+1.00%", "-2.50%", "0.00%"], "quote": ["—", "—", "—"]},
        )
        source = (STATIC / "js/app.js").read_text()
        self.assertLess(
            source.index("clearStockQuote(elements)"),
            source.index("await api.getStock(ticker)"),
        )

    def test_stale_and_inactive_statuses_remain_distinct_from_shape(self):
        module_uri = (STATIC / "js/universe.js").as_uri()
        script = f"""
            import * as universeModule from {json.dumps(module_uri)};
            const describe = universeModule.describeTickerState || (() => null);
            console.log(JSON.stringify([
              describe({{stale: false, inactive: false, shape_state: 'strict_vcp'}}, 'en'),
              describe({{stale: true, inactive: false, shape_state: 'tight_platform'}}, 'en'),
              describe({{stale: false, inactive: true, shape_state: 'near_pivot'}}, 'en'),
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
            json.loads(result.stdout),
            [
                {"status": "Current", "shape": "Strict VCP"},
                {"status": "Stale", "shape": "Tight platform"},
                {"status": "Inactive", "shape": "Near pivot"},
            ],
        )

    def test_linked_chart_contract(self):
        source = (STATIC / "js/charts.js").read_text()

        for marker in (
            "export function createLinkedCharts",
            "LightweightCharts.CandlestickSeries",
            "LightweightCharts.HistogramSeries",
            "LightweightCharts.LineSeries",
            'title: "EMA20"',
            'title: "SMA50"',
            'title: "SMA200"',
            "createPriceLine",
            "subscribeCrosshairMove",
            "subscribeClick",
            "subscribeVisibleLogicalRangeChange",
            "setVisibleLogicalRange",
            "syncing",
            "daily_return",
            "true_range_pct",
            "volume_change",
            "volume_ratio",
            "volume_ma20",
            "atr20",
            "pivot_distance_pct",
        ):
            self.assertIn(marker, source)

        self.assertEqual(source.count("LightweightCharts.CandlestickSeries"), 1)
        self.assertEqual(source.count("LightweightCharts.HistogramSeries"), 1)
        self.assertEqual(source.count("LightweightCharts.LineSeries"), 5)
        for title in (
            "Strict VCP pivot",
            "Tight-platform pivot",
            "Volume MA20",
            "Volume ratio",
        ):
            self.assertIn(title, source)
        for field in (
            "volume_ratio_change",
            "pivot_distance_change_pct",
            "ema20_cross",
            "sma50_cross",
        ):
            self.assertIn(field, source)
        self.assertIn("createSeriesMarkers", source)
        for range_name, bars in (("3m", 63), ("6m", 126), ("1y", 252), ("2y", 504)):
            self.assertIn(f'"{range_name}": {bars}', source)

        app_source = (STATIC / "js/app.js").read_text()
        self.assertIn('from "./charts.js"', app_source)
        self.assertIn("chartController.setChartData(payload)", app_source)

        html = HTML.read_text()
        self.assertIn('data-range="3m"', html)
        self.assertIn('data-range="6m"', html)
        self.assertIn('data-range="1y"', html)
        self.assertIn('data-range="2y"', html)
        self.assertIn('data-range="all"', html)

    def test_chart_adapter_plots_shape_levels_annotations_and_volume_diagnostics(self):
        module_uri = (STATIC / "js/charts.js").as_uri()
        script = f"""
            const created = [];
            const markerSets = [];
            function node() {{
              return {{textContent: '', className: '', children: [], dataset: {{}},
                append(...items) {{ this.children.push(...items); }},
                replaceChildren(...items) {{ this.children = [...items]; this.textContent = ''; }} }};
            }}
            globalThis.document = {{ createElement: () => node() }};
            function chart(name) {{
              const series = [];
              const priceLines = [];
              const scale = {{subscribeVisibleLogicalRangeChange() {{}},
                unsubscribeVisibleLogicalRangeChange() {{}}, setVisibleLogicalRange() {{}}, fitContent() {{}}}};
              const value = {{name, series, priceLines, timeScale: () => scale,
                addSeries(type, options) {{
                  const item = {{type, options, data: [], setData(data) {{ this.data = data; }},
                    createPriceLine(line) {{ priceLines.push(line); return line; }}, removePriceLine() {{}}}};
                  series.push(item); return item;
                }}, subscribeCrosshairMove() {{}}, unsubscribeCrosshairMove() {{}},
                subscribeClick() {{}}, unsubscribeClick() {{}}, applyOptions() {{}}, remove() {{}},
                setCrosshairPosition() {{}}, clearCrosshairPosition() {{}}}};
              created.push(value); return value;
            }}
            globalThis.LightweightCharts = {{
              CandlestickSeries: 'candles', HistogramSeries: 'histogram', LineSeries: 'line',
              CrosshairMode: {{Normal: 0}}, LineStyle: {{Dashed: 2}},
              createChart(element) {{ return chart(element.name); }},
              createSeriesMarkers(_series, markers) {{
                const controller = {{markers, setMarkers(next) {{ this.markers = next; markerSets.push(next); }}}};
                markerSets.push(markers); return controller;
              }},
            }};
            const chartModule = await import({json.dumps(module_uri)});
            const {{ createLinkedCharts }} = chartModule;
            const detail = node();
            const controller = createLinkedCharts(
              {{name: 'price', clientWidth: 800, clientHeight: 400}},
              {{name: 'volume', clientWidth: 800, clientHeight: 180}}, detail,
            );
            const row = {{time: '2026-07-22', open: 99, high: 102, low: 98, close: 101,
              volume: 1200, volume_ma20: 1000, volume_ratio: 1.2, volume_ratio_change: 0.15,
              ema20: 100, sma50: 95, sma200: 90, daily_return: 0.01, true_range_pct: 4,
              volume_change: 0.1, atr20: 3, pivot: 100, pivot_distance_pct: 1,
              pivot_distance_change_pct: 0.75, ema20_cross: 'above', sma50_cross: null}};
            controller.setChartData({{chart: [row], structures: {{key_levels: {{
              strict_vcp_pivot: 103, tight_platform_pivot: 104,
            }}, annotations: [{{time: row.time, type: 'strict_vcp', label: 'Strict VCP'}}]}}}});
            console.log(JSON.stringify({{
              priceLines: created[0].priceLines.map(line => line.title),
              volumeLines: created[1].series.filter(series => series.type === 'line')
                .map(series => [series.options.title, series.data]),
              markers: markerSets.at(-1),
              detail: chartModule.detailItems
                ? chartModule.detailItems(row).map(item => [item.label, item.value]) : [],
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        actual = json.loads(result.stdout)
        self.assertEqual(actual["priceLines"], ["Strict VCP pivot", "Tight-platform pivot"])
        self.assertEqual(
            [line[0] for line in actual["volumeLines"]],
            ["Volume MA20", "Volume ratio"],
        )
        self.assertEqual(actual["markers"][0]["text"], "Strict VCP")
        details = dict(actual["detail"])
        self.assertEqual(details["Volume ratio change"], "+0.15×")
        self.assertEqual(details["Pivot-distance change"], "+0.75 pp")
        self.assertEqual(details["EMA20 cross"], "Crossed above")

    def test_chart_panels_contain_canvas_intrinsic_width_on_mobile(self):
        css = (STATIC / "css/dashboard.css").read_text()

        selectors = (
            ".research-panel > .panel",
            ".chart-placeholder, .volume-placeholder, .scenario-placeholder",
            ".scenario-layout, .scenario-layout > *",
        )
        for selector in selectors:
            match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
            self.assertIsNotNone(match, selector)
            declarations = match.group(1)
            self.assertIn("min-width: 0", declarations)
            self.assertIn("max-width: 100%", declarations)
        for selector in selectors[:2]:
            match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
            self.assertIn("overflow: hidden", match.group(1))

    def test_research_grid_track_has_zero_intrinsic_minimum(self):
        css = (STATIC / "css/dashboard.css").read_text()

        match = re.search(r"\.research-panel\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", match.group(1))

    def test_analysis_grid_and_panels_have_zero_intrinsic_minimum(self):
        css = (STATIC / "css/dashboard.css").read_text()

        self.assertIn(
            ".security-header, .analysis-grid { grid-template-columns: minmax(0, 1fr); }",
            css,
        )
        match = re.search(r"\.analysis-grid > \.panel\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match)
        self.assertIn("min-width: 0", match.group(1))

    def test_factor_helpers_are_payload_driven_and_preserve_diagnostics(self):
        module_uri = (STATIC / "js/factors.js").as_uri()
        script = f"""
            import * as factorModule from {json.dumps(module_uri)};
            const {{ factorDetailRows, groupFactorResults }} = factorModule;
            const factors = [
              {{key: 'fresh_factor', label: 'Fresh factor', group: 'trend', overview: true,
                raw_value: 1.25, formatted: '1.25x', percentile: 0.75,
                peer_count: 11, display_score: 75,
                observation_date: '2026-07-22', missing: false,
                missing_reason: null, description: 'A registry factor.',
                methodology: 'Rank exact-date observations.', version: 'v2'}},
              {{key: 'future_factor', label: 'Future factor', group: 'future_lab', overview: false,
                raw_value: {{window: 12}}, formatted: null, percentile: null,
                peer_count: 4,
                display_score: null, observation_date: '2026-07-22', missing: false,
                missing_reason: null, description: 'Added after this UI.',
                methodology: 'Future method.', version: 'v1'}},
              {{key: 'future_visible', label: 'Future visible', group: 'future_visible', overview: true,
                raw_value: 2, formatted: '2', percentile: 0.8, peer_count: 7,
                display_score: 80, observation_date: '2026-07-22', missing: false,
                missing_reason: null, description: 'Opted in.', methodology: 'Visible method.', version: 'v1'}},
              {{key: 'missing_factor', label: 'Missing factor', group: 'risk', overview: true,
                raw_value: null, formatted: null, percentile: null,
                display_score: null, observation_date: '2026-07-22', missing: true,
                missing_reason: 'missing_benchmark', description: 'Needs a peer.',
                methodology: 'Compare to benchmark.', version: 'v3'}}
            ];
            const metadata = [
              {{key: 'risk', label: 'Risk', methodology: 'Risk method.', overview: true}},
              {{key: 'trend', label: 'Trend', methodology: 'Trend method.', overview: true}},
              {{key: 'future_lab', label: 'Future lab', methodology: 'Hidden method.', overview: false}},
              {{key: 'future_visible', label: 'Future visible', methodology: 'Visible method.', overview: true}},
            ];
            const groups = groupFactorResults(factors, metadata);
            const rows = factorDetailRows(factors);
            const overview = factorModule.overviewFactorGroups
              ? factorModule.overviewFactorGroups(factors, metadata) : [];
            console.log(JSON.stringify({{
              groups: groups.map(group => [group.label, group.factors.map(row => row.key)]),
              overview: overview.map(group => [group.label, group.factors.map(row => row.key)]),
              groupMethodology: groups.map(group => group.methodology),
              raw: rows.map(row => row.rawValue),
              percentiles: rows.map(row => row.percentile),
              missingReason: rows[3].missingReason,
              descriptions: rows.map(row => row.description),
              methodologies: rows.map(row => row.methodology),
              versions: rows.map(row => row.version),
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
                "groups": [
                    ["Risk", ["missing_factor"]],
                    ["Trend", ["fresh_factor"]],
                    ["Future lab", ["future_factor"]],
                    ["Future visible", ["future_visible"]],
                ],
                "overview": [
                    ["Trend", ["fresh_factor"]],
                    ["Future visible", ["future_visible"]],
                ],
                "groupMethodology": [
                    "Risk method.",
                    "Trend method.",
                    "Hidden method.",
                    "Visible method.",
                ],
                "raw": ["1.25", '{"window":12}', "2", "null"],
                "percentiles": [
                    "75th percentile · 11 same-date peers",
                    "Unavailable · 4 same-date peers",
                    "80th percentile · 7 same-date peers",
                    "Unavailable · peer count unavailable",
                ],
                "missingReason": "missing benchmark",
                "descriptions": [
                    "A registry factor.",
                    "Added after this UI.",
                    "Opted in.",
                    "Needs a peer.",
                ],
                "methodologies": [
                    "Rank exact-date observations.",
                    "Future method.",
                    "Visible method.",
                    "Compare to benchmark.",
                ],
                "versions": ["v2", "v1", "v1", "v3"],
            },
        )

        source = (STATIC / "js/factors.js").read_text()
        for hard_coded_factor_key in (
            "close_vs_ema20_pct",
            "strict_vcp",
            "tight_platform",
            "legacy_score",
        ):
            self.assertNotIn(hard_coded_factor_key, source)
        for hard_coded_group in ('["trend"', '["momentum"', '["structure"', '["volume"', '["risk"', '["legacy"'):
            self.assertNotIn(hard_coded_group, source)

    def test_scenario_helpers_use_historical_labels_and_report_metadata(self):
        module_uri = (STATIC / "js/scenarios.js").as_uri()
        script = f"""
            import {{ scenarioView }} from {json.dumps(module_uri)};
            const payload = {{
              provider: 'historical_distribution',
              observation_date: '2026-07-22',
              methodology: 'Point-in-time non-overlapping samples.',
              horizons: {{
                '20': {{available: true, horizon_sessions: 20, sample_count: 12,
                  methodology: 'Twelve samples.', paths: {{
                    pessimistic: [{{session: 0, price: 100}}, {{session: 20, price: 90}}],
                    median: [{{session: 0, price: 100}}, {{session: 20, price: 103}}],
                    optimistic: [{{session: 0, price: 100}}, {{session: 20, price: 111}}]
                  }}}},
                '40': {{available: false, horizon_sessions: 40, sample_count: 4,
                  missing_reason: 'insufficient_samples', methodology: 'Needs more samples.', paths: {{}}}}
              }}
            }};
            const view = scenarioView(payload, 'en');
            console.log(JSON.stringify({{
              titles: view.series.map(series => series.title),
              points: view.series.map(series => series.data.length),
              meta: view.horizons.map(item => [item.label, item.sampleText, item.detail]),
              methodology: view.methodology,
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
                "titles": [
                    "20 sessions · Pessimistic historical scenario",
                    "20 sessions · Median historical scenario",
                    "20 sessions · Optimistic historical scenario",
                ],
                "points": [2, 2, 2],
                "meta": [
                    ["20 sessions", "12 non-overlapping samples", "Twelve samples."],
                    ["40 sessions", "4 non-overlapping samples", "insufficient samples"],
                ],
                "methodology": "Point-in-time non-overlapping samples.",
            },
        )

    def test_update_controller_polls_running_jobs_and_exposes_429_resume(self):
        module_uri = (STATIC / "js/update.js").as_uri()
        i18n_uri = (STATIC / "js/i18n.js").as_uri()
        script = f"""
            import {{ setLocale }} from {json.dumps(i18n_uri)};
            import {{
              createUpdateController, shouldReloadSelectedTicker, updateRetryDelay
            }} from {json.dumps(module_uri)};
            setLocale('en');
            const button = {{disabled: false, textContent: '', dataset: {{}},
              addEventListener(_name, handler) {{ this.handler = handler; }}}};
            const status = {{textContent: '', dataset: {{}}}};
            const timers = [];
            const terminal = [];
            const outcomes = [
              new Error('unsafe /Users/alice/update.log'),
              {{state: 'running', total: 3, completed: 1, updated: 1,
                current_ticker: 'BBB', error: null, resumable: false}},
              {{state: 'rate_limited', total: 3, completed: 1, updated: 1,
                current_ticker: 'BBB', error: 'rate_limited', resumable: true}},
            ];
            const client = {{
              async startUpdate() {{ return {{state: 'running', total: 3, completed: 0,
                updated: 0, current_ticker: 'AAA', error: null, resumable: false}}; }},
              async getUpdateStatus() {{
                const outcome = outcomes.shift();
                if (outcome instanceof Error) throw outcome;
                return outcome;
              }},
            }};
            const controller = createUpdateController({{
              button, status, apiClient: client,
              schedule(callback, delay) {{ timers.push({{callback, delay}}); return timers.length; }},
              cancel() {{}},
              async onTerminal(snapshot) {{ terminal.push(snapshot.state); }},
            }});
            await controller.start();
            const first = timers.shift();
            await first.callback();
            const retryState = {{
              text: status.textContent,
              disabled: button.disabled,
              tone: status.dataset.tone,
            }};
            const second = timers.shift();
            await second.callback();
            const third = timers.shift();
            await third.callback();
            console.log(JSON.stringify({{
              delays: [first.delay, second.delay, third.delay],
              timerCount: timers.length,
              terminal,
              retryState,
              buttonText: button.textContent,
              buttonDisabled: button.disabled,
              statusText: status.textContent,
              terminalPredicate: [
                controller.isTerminal('idle'), controller.isTerminal('running'),
                controller.isTerminal('completed'), controller.isTerminal('partial'),
                controller.isTerminal('rate_limited'), controller.isTerminal('failed')
              ],
              reloadPredicate: [
                shouldReloadSelectedTicker('AAA', '2026-07-22', [
                  {{ticker: 'AAA', latest_date: '2026-07-22'}}
                ]),
                shouldReloadSelectedTicker('AAA', '2026-07-22', [
                  {{ticker: 'AAA', latest_date: '2026-07-23'}}
                ]),
                shouldReloadSelectedTicker('AAA', '2026-07-22', [
                  {{ticker: 'BBB', latest_date: '2026-07-23'}}
                ])
              ],
              retryDelays: [1, 2, 3, 4, 20].map(updateRetryDelay),
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        actual = json.loads(result.stdout)
        self.assertEqual(actual["delays"], [1000, 2000, 1000])
        self.assertEqual(actual["timerCount"], 0)
        self.assertEqual(actual["terminal"], ["rate_limited"])
        self.assertEqual(
            actual["retryState"],
            {
                "text": "Update status is temporarily unavailable; still running and retrying.",
                "disabled": True,
                "tone": "warning",
            },
        )
        self.assertNotIn("/Users/", actual["retryState"]["text"])
        self.assertEqual(actual["buttonText"], "Resume price update")
        self.assertFalse(actual["buttonDisabled"])
        self.assertIn("Rate limited after 1/3", actual["statusText"])
        self.assertNotIn("complete", actual["statusText"].lower())
        self.assertEqual(actual["terminalPredicate"], [True, False, True, True, True, True])
        self.assertEqual(actual["reloadPredicate"], [False, True, False])
        self.assertEqual(actual["retryDelays"], [2000, 4000, 5000, 5000, 5000])

    def test_update_controller_recovers_running_status_during_initialization(self):
        module_uri = (STATIC / "js/update.js").as_uri()
        i18n_uri = (STATIC / "js/i18n.js").as_uri()
        script = f"""
            import {{ setLocale }} from {json.dumps(i18n_uri)};
            import {{ createUpdateController }} from {json.dumps(module_uri)};
            setLocale('en');
            const button = {{disabled: false, textContent: '', dataset: {{}},
              addEventListener() {{}}, removeEventListener() {{}}}};
            const status = {{textContent: '', dataset: {{}}}};
            const timers = [];
            let statusCalls = 0;
            const controller = createUpdateController({{
              button, status,
              apiClient: {{
                async getUpdateStatus() {{
                  statusCalls += 1;
                  return {{state: 'running', total: 8, completed: 3, updated: 2,
                    current_ticker: 'MSFT', error: null, resumable: false}};
                }},
              }},
              schedule(callback, delay) {{ timers.push({{callback, delay}}); return timers.length; }},
              cancel() {{}},
            }});
            const initialized = typeof controller.initialize === 'function';
            if (initialized) await controller.initialize();
            console.log(JSON.stringify({{
              initialized, statusCalls, disabled: button.disabled, buttonText: button.textContent,
              statusText: status.textContent, timers: timers.map(timer => timer.delay),
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )
        actual = json.loads(result.stdout)
        self.assertTrue(actual["initialized"])
        self.assertEqual(actual["statusCalls"], 1)
        self.assertTrue(actual["disabled"])
        self.assertEqual(actual["buttonText"], "Update market data")
        self.assertIn("3/8 checked", actual["statusText"])
        self.assertEqual(actual["timers"], [1000])

        app_source = (STATIC / "js/app.js").read_text()
        self.assertIn("await updateController.initialize()", app_source)

    def test_task_ten_modules_are_integrated_with_the_dashboard(self):
        app_source = (STATIC / "js/app.js").read_text()
        for marker in (
            'from "./factors.js"',
            'from "./scenarios.js"',
            'from "./update.js"',
            "renderFactors(payload.factors",
            "renderStructures(payload.structures",
            "renderScenarios(payload.scenarios",
            "createUpdateController",
            "groupMetadata: store.getState().universePayload?.factor_groups",
        ):
            self.assertIn(marker, app_source)

        api_source = (STATIC / "js/api.js").read_text()
        self.assertIn('requestJson("/api/update", { method: "POST" })', api_source)
        self.assertIn('requestJson("/api/update/status")', api_source)

        html = HTML.read_text()
        self.assertIn("<details", html)
        self.assertIn(
            '<summary data-i18n="factor.details">Factor detail table</summary>',
            html,
        )
        for heading in (
            "Formatted value",
            "Raw value",
            "Percentile / peers",
            "Display score",
            "Description / version",
            "Methodology",
            "Missing reason",
        ):
            self.assertIn(heading, html)


if __name__ == "__main__":
    unittest.main()
