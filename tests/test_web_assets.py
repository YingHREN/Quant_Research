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
    def run_dashboard_runtime(self, mode):
        result = subprocess.run(
            [
                "node",
                str(ROOT / "tests/dashboard_runtime.mjs"),
                (STATIC / "js/app.js").as_uri(),
                mode,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

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
              i18n.formatFullDate("2026-07-17T14:35:22.123+08:00"),
              "2026-07-17",
            );
            for (const invalid of [
              "2026-07-17Tgarbage",
              "2026-07-17T25:00:00",
              "2026-07-17T12:60:00",
              "2026-07-17T12:30:60",
              "2026-07-17T12:30:00+24:00",
            ]) {{
              assert.equal(i18n.formatFullDate(invalid), "—", invalid);
              assert.equal(i18n.formatChartTickDate(invalid), "—", invalid);
            }}
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

    def test_actual_dashboard_locale_switch_refreshes_dynamic_renderers(self):
        actual = self.run_dashboard_runtime("success")
        self.assertIn("收盘价相对 EMA20", actual["tableZh"])
        self.assertIn("Close vs EMA20", actual["tableEn"])
        self.assertIn("开盘价", actual["chartZh"])
        self.assertIn("Open", actual["chartEn"])

    def test_actual_dashboard_locale_switch_preserves_safe_error_states(self):
        universe = self.run_dashboard_runtime("universe-error")
        self.assertEqual(universe["zh"]["universeTone"], "error")
        self.assertEqual(universe["en"]["researchTone"], "error")
        stock = self.run_dashboard_runtime("stock-error")
        self.assertNotIn("An internal error occurred", stock.values())
        unknown = self.run_dashboard_runtime("stock-unknown-error")
        self.assertNotIn("/Users/", " ".join(unknown.values()))

    def test_known_update_errors_are_localized_and_unknown_errors_remain_safe_fallbacks(self):
        module_uri = (STATIC / "js/update.js").as_uri()
        i18n_uri = (STATIC / "js/i18n.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            import {{ setLocale }} from {json.dumps(i18n_uri)};
            import {{ createUpdateController }} from {json.dumps(module_uri)};

            function element() {{
              return {{disabled: false, textContent: '', dataset: {{}}, listeners: {{}},
                addEventListener(name, handler) {{ this.listeners[name] = handler; }},
                removeEventListener() {{}}, removeAttribute() {{}}}};
            }}
            async function exercise(error) {{
              const button = element();
              const status = element();
              const controller = createUpdateController({{button, status, apiClient: {{
                async startUpdate() {{ throw error; }}
              }}, schedule() {{}}, cancel() {{}}}});
              await controller.start();
              const zh = status.textContent;
              setLocale('en');
              const en = status.textContent;
              controller.destroy();
              setLocale('zh-CN');
              return {{zh, en}};
            }}
            const known = await exercise({{
              code: 'internal_error', message: 'An internal error occurred'
            }});
            const unknown = await exercise({{
              code: 'future_error', message: 'unsafe /Users/alice/private.db detail'
            }});
            const collision = await exercise({{
              code: 'constructor', message: 'unsafe constructor detail'
            }});
            const nullError = await exercise(null);
            const undefinedError = await exercise(undefined);
            assert.deepEqual(known, {{
              zh: '本地仪表板遇到内部错误。',
              en: 'The local dashboard encountered an internal error.',
            }});
            assert.deepEqual(unknown, {{
              zh: '无法启动价格更新',
              en: 'Unable to start price update',
            }});
            assert.deepEqual(collision, {{
              zh: '无法启动价格更新',
              en: 'Unable to start price update',
            }});
            assert.deepEqual(nullError, {{
              zh: '无法启动价格更新', en: 'Unable to start price update',
            }});
            assert.deepEqual(undefinedError, {{
              zh: '无法启动价格更新', en: 'Unable to start price update',
            }});
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_factor_localization_uses_stable_keys_with_safe_unknown_fallbacks(self):
        module_uri = (STATIC / "js/factors.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            import {{ factorDetailRows, groupFactorResults, renderFactors, renderStructures }}
              from {json.dumps(module_uri)};
            function node(tagName = 'div') {{
              return {{tagName, textContent: '', className: '', children: [], dataset: {{}},
                attributes: {{}}, style: {{}}, append(...items) {{ this.children.push(...items); }},
                replaceChildren(...items) {{ this.children = [...items]; this.textContent = ''; }},
                setAttribute(name, value) {{ this.attributes[name] = String(value); }},
                getAttribute(name) {{ return this.attributes[name]; }}}};
            }}
            globalThis.document = {{createElement: node, createDocumentFragment: () => node('fragment')}};
            const known = {{key: 'close_vs_ema20_pct', label: 'Close vs EMA20', group: 'trend',
              overview: true, raw_value: null, formatted: null, percentile: null, peer_count: 3,
              display_score: null, observation_date: '2026-07-22', missing: true,
              missing_reason: 'missing_value', description: 'Close relative to the point-in-time 20-session EMA.',
              methodology: 'Close divided by the 20-session exponential moving average, minus one, expressed in percent.',
              version: 'builtin-v1'}};
            const unknown = {{...known, key: 'future_factor', label: 'Future factor', group: 'future',
              description: 'Safe future description.', methodology: 'Safe future methodology.',
              missing_reason: 'future_reason'}};
            const metadata = [{{key: 'trend', label: 'Trend',
              methodology: 'Moving-average position diagnostics.', overview: true}},
              {{key: 'future', label: 'Future group', methodology: 'Future group methodology.', overview: true,
                i18n: {{'zh-CN': {{label: '未来分组', methodology: '未来分组方法。'}}}}}}];
            const zhRows = factorDetailRows([known, unknown], 'zh-CN');
            const zhGroups = groupFactorResults([known, unknown], metadata, 'zh-CN');
            assert.equal(zhRows[0].label, '收盘价相对 EMA20');
            assert.equal(zhRows[0].percentile, '百分位不可用 · 3 个同日样本');
            assert.equal(zhRows[0].missingReason, '缺少因子值');
            assert.equal(zhRows[1].label, 'Future factor');
            assert.equal(zhRows[1].description, 'Safe future description.');
            assert.equal(zhRows[1].methodology, 'Safe future methodology.');
            assert.equal(zhRows[1].missingReason, 'future reason');
            assert.deepEqual(zhGroups.map(group => [group.label, group.methodology]), [
              ['趋势', '均线位置诊断。'], ['未来分组', '未来分组方法。'],
            ]);
            const overview = node();
            const tableBody = node('tbody');
            renderFactors([], {{overview, tableBody, locale: 'zh-CN'}});
            assert.equal(overview.textContent, '当前观察没有可用的数值展示分数。');
            assert.equal(tableBody.children[0].children[0].textContent, '没有可用的因子诊断。');
            const structure = node();
            renderStructures(null, structure, 'zh-CN');
            assert.equal(structure.textContent, '没有可用的结构诊断。');
            renderStructures({{future_metric: 42}}, structure, 'zh-CN');
            assert.equal(structure.children[0].children[0].children[0].textContent, 'Future Metric');
            renderStructures({{price: 12.627995, active: false, marks: []}}, structure, 'zh-CN');
            const structureItems = structure.children[0].children;
            assert.equal(structureItems[0].children[1].textContent, '12.63');
            assert.equal(structureItems[1].children[1].textContent, '否');
            assert.equal(structureItems[2].children[1].textContent, '—');
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_scenario_methodology_localizes_known_provider_and_preserves_unknown(self):
        module_uri = (STATIC / "js/scenarios.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            import {{ scenarioView }} from {json.dumps(module_uri)};
            const known = scenarioView({{
              provider: 'historical_distribution',
              methodology: 'English known methodology from the server.',
              horizons: {{'20': {{available: true, horizon_sessions: 20, sample_count: 12,
                methodology: 'English horizon methodology.', paths: {{}}}}}},
            }}, 'zh-CN');
            const unknown = scenarioView({{
              provider: 'future_provider', methodology: 'Safe future methodology.', horizons: {{}}
            }}, 'zh-CN');
            assert.equal(known.methodology,
              '基于观察日可用的非重叠周期收益构建描述性历史情景；并非预测或概率。');
            assert.equal(known.horizons[0].detail,
              '12 个非重叠的 20 日历史收益样本；绝对分位数上限为当前 63 日已实现波动率缩放值的三倍。');
            assert.equal(unknown.methodology, 'Safe future methodology.');
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )

    def test_factor_popover_supports_pointer_keyboard_aria_and_cleanup(self):
        module_uri = (STATIC / "js/factors.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';

            class Node {{
              constructor(tagName = 'div') {{
                this.tagName = tagName.toUpperCase(); this.id = ''; this.className = '';
                this.children = []; this.parentNode = null; this.attributes = {{}};
                this.dataset = {{}}; this.style = {{}}; this.hidden = false;
                this.listeners = new Map(); this._textContent = ''; this.focused = false;
                this.rect = {{left: 380, right: 400, top: 560, bottom: 580, width: 20, height: 20}};
              }}
              get textContent() {{ return this._textContent; }}
              set textContent(value) {{ this._textContent = value == null ? '' : String(value); this.children = []; }}
              append(...items) {{ items.forEach(item => {{ item.parentNode = this; this.children.push(item); }}); }}
              replaceChildren(...items) {{ this._textContent = ''; this.children = []; this.append(...items); }}
              setAttribute(name, value) {{ this.attributes[name] = String(value); if (name === 'id') this.id = String(value); }}
              getAttribute(name) {{ return this.attributes[name]; }}
              removeAttribute(name) {{ delete this.attributes[name]; }}
              addEventListener(name, handler) {{
                if (!this.listeners.has(name)) this.listeners.set(name, []);
                this.listeners.get(name).push(handler);
              }}
              dispatch(name, extra = {{}}) {{
                const event = {{currentTarget: this, target: this, key: extra.key,
                  relatedTarget: extra.relatedTarget || null, defaultPrevented: false,
                  preventDefault() {{ this.defaultPrevented = true; }}}};
                for (const handler of this.listeners.get(name) || []) handler(event);
                return event;
              }}
              contains(target) {{ return target === this || this.children.some(child => child.contains(target)); }}
              getBoundingClientRect() {{ return this.rect; }}
              focus() {{ this.focused = true; this.dispatch('focus'); }}
            }}
            function descendants(node) {{ return node.children.flatMap(child => [child, ...descendants(child)]); }}
            function byClass(node, name) {{ return descendants(node).filter(child => child.className === name); }}
            function treeText(node) {{ return [node.textContent, ...node.children.map(treeText)].join(' ').replace(/\\s+/g, ' ').trim(); }}

            const body = new Node('body');
            const documentListeners = new Map();
            let nextTimer = 1;
            const timers = new Map();
            globalThis.setTimeout = handler => {{ const id = nextTimer++; timers.set(id, handler); return id; }};
            globalThis.clearTimeout = id => timers.delete(id);
            const runTimers = () => {{
              const pending = [...timers.values()]; timers.clear(); pending.forEach(handler => handler());
            }};
            globalThis.document = {{body, createElement: tag => new Node(tag),
              createDocumentFragment: () => new Node('fragment'), getElementById: () => null,
              addEventListener(name, handler) {{ documentListeners.set(name, handler); }},
              removeEventListener(name, handler) {{ if (documentListeners.get(name) === handler) documentListeners.delete(name); }},
              dispatch(name, target, key = null) {{
                const event = {{target, key, preventDefault() {{ this.defaultPrevented = true; }}}};
                documentListeners.get(name)?.(event); return event;
              }}}};
            globalThis.window = {{innerWidth: 400, innerHeight: 600,
              addEventListener() {{}}, removeEventListener() {{}}}};

            const {{renderFactors}} = await import({json.dumps(module_uri)});
            const overview = new Node('div');
            const tableBody = new Node('tbody');
            const base = {{group: 'future', overview: true, percentile: 0.75, peer_count: 2,
              display_score: 75, raw_value: 1.25, formatted: '1.25x',
              observation_date: '2026-07-22', missing: false, missing_reason: null,
              description: 'English meaning.', methodology: 'English method.',
              window: '20 sessions', direction: 'higher', version: 'v2'}};
            const translated = {{...base, key: 'future_factor', label: 'Future factor',
              i18n: {{'zh-CN': {{label: '未来因子 <img src=x>', description: '中文含义 <script>',
                methodology: '中文方法', window: '20 个交易日', direction: '数值越高越强'}}}}}};
            const missing = {{...base, key: 'missing_factor', label: 'Missing factor',
              raw_value: null, formatted: null, display_score: null,
              missing: true, missing_reason: 'missing_benchmark',
              version: 'v3'}};
            const groupMetadata = [{{key: 'future', label: 'Future',
              methodology: 'Future methodology.', overview: true}}];
            renderFactors([translated, missing], {{overview, tableBody, groupMetadata, locale: 'zh-CN'}});

            const buttons = [...byClass(overview, 'factor-info'), ...byClass(tableBody, 'factor-info')];
            assert.equal(buttons.length, 3);
            assert.ok(buttons.every(button => button.tagName === 'BUTTON'));
            assert.ok(buttons.every(button => button.getAttribute('type') === 'button'));
            assert.ok(buttons.every(button => button.getAttribute('aria-controls') === 'factor-popover'));
            assert.ok(buttons.every(button => button.getAttribute('aria-describedby') === 'factor-popover'));
            assert.ok(buttons.every(button => button.getAttribute('aria-expanded') === 'false'));
            assert.match(buttons[0].getAttribute('aria-label'), /未来因子/);
            assert.equal(byClass(body, 'factor-popover').length, 1);
            const popover = byClass(body, 'factor-popover')[0];
            popover.rect = {{left: 0, right: 220, top: 0, bottom: 160, width: 220, height: 160}};
            assert.equal(popover.getAttribute('role'), 'tooltip');
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('pointerenter');
            assert.equal(popover.hidden, false);
            assert.equal(buttons[0].getAttribute('aria-expanded'), 'true');
            assert.match(treeText(popover), /未来因子 <img src=x>/);
            assert.match(treeText(popover), /中文含义 <script>/);
            assert.match(treeText(popover), /当前值 1.25x/);
            assert.match(treeText(popover), /数据日期 2026-07-22/);
            assert.match(treeText(popover), /版本 v2/);
            assert.match(treeText(popover), /20 个交易日/);
            assert.match(treeText(popover), /数值越高越强/);
            assert.equal(popover.style.left, '172px');
            assert.equal(popover.style.top, '392px');
            document.dispatch('keydown', body, 'Escape');
            assert.equal(popover.hidden, true);
            assert.equal(buttons[0].focused, false);

            buttons[0].dispatch('pointerenter');
            buttons[0].dispatch('pointerleave');
            assert.equal(popover.hidden, false);
            popover.dispatch('pointerenter');
            runTimers();
            assert.equal(popover.hidden, false);
            popover.dispatch('pointerleave');
            assert.equal(popover.hidden, false);
            runTimers();
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('click');
            buttons[0].dispatch('pointerleave');
            popover.dispatch('pointerenter');
            runTimers();
            popover.dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, false);
            buttons[0].dispatch('click');
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('focus');
            assert.equal(popover.hidden, false);
            buttons[0].dispatch('blur');
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('focus');
            buttons[0].dispatch('pointerenter');
            buttons[0].dispatch('pointerleave');
            popover.dispatch('pointerenter');
            runTimers();
            popover.dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, false);
            buttons[0].dispatch('blur');
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('pointerenter');
            buttons[0].dispatch('focus');
            buttons[0].dispatch('blur');
            assert.equal(popover.hidden, false);
            buttons[0].dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('focus');
            buttons[2].dispatch('pointerenter');
            assert.match(treeText(popover), /缺失原因 缺少基准数据/);
            buttons[2].dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, false);
            assert.equal(buttons[0].getAttribute('aria-expanded'), 'true');
            assert.equal(buttons[2].getAttribute('aria-expanded'), 'false');
            assert.match(treeText(popover), /未来因子 <img src=x>/);
            buttons[0].dispatch('blur');
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('pointerenter');
            buttons[2].dispatch('focus');
            assert.match(treeText(popover), /缺失原因 缺少基准数据/);
            buttons[2].dispatch('blur');
            assert.equal(popover.hidden, false);
            assert.equal(buttons[0].getAttribute('aria-expanded'), 'true');
            assert.equal(buttons[2].getAttribute('aria-expanded'), 'false');
            assert.match(treeText(popover), /未来因子 <img src=x>/);
            buttons[0].dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('focus');
            document.dispatch('keydown', body, 'Escape');
            assert.equal(popover.hidden, true);
            buttons[2].dispatch('pointerenter');
            assert.match(treeText(popover), /缺失原因 缺少基准数据/);
            buttons[2].dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, true);
            assert.equal(buttons[0].getAttribute('aria-expanded'), 'false');
            buttons[0].dispatch('blur');
            buttons[0].dispatch('focus');
            assert.equal(popover.hidden, false);
            assert.match(treeText(popover), /未来因子 <img src=x>/);
            buttons[0].dispatch('blur');
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('focus');
            buttons[0].dispatch('click');
            buttons[0].dispatch('click');
            assert.equal(popover.hidden, true);
            buttons[2].dispatch('pointerenter');
            buttons[2].dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, true);
            assert.equal(buttons[0].getAttribute('aria-expanded'), 'false');
            buttons[0].dispatch('blur');
            buttons[0].dispatch('pointerenter');
            assert.equal(popover.hidden, false);
            assert.match(treeText(popover), /未来因子 <img src=x>/);
            buttons[0].dispatch('pointerleave');
            runTimers();
            assert.equal(popover.hidden, true);

            let keyEvent = buttons[0].dispatch('keydown', {{key: 'Enter'}});
            assert.equal(keyEvent.defaultPrevented, true);
            assert.equal(popover.hidden, false);
            buttons[0].dispatch('keydown', {{key: 'Enter'}});
            assert.equal(popover.hidden, true);
            keyEvent = buttons[0].dispatch('keydown', {{key: ' '}});
            assert.equal(keyEvent.defaultPrevented, true);
            assert.equal(popover.hidden, false);

            buttons[2].dispatch('click');
            assert.equal(buttons[0].getAttribute('aria-expanded'), 'false');
            assert.equal(buttons[2].getAttribute('aria-expanded'), 'true');
            assert.equal(byClass(body, 'factor-popover').length, 1);
            assert.match(treeText(popover), /缺失原因 缺少基准数据/);
            document.dispatch('keydown', body, 'Escape');
            assert.equal(popover.hidden, true);
            assert.equal(buttons[2].getAttribute('aria-expanded'), 'false');
            assert.equal(buttons[2].focused, true);

            buttons[1].dispatch('click');
            document.dispatch('click', new Node('main'));
            assert.equal(popover.hidden, true);

            buttons[0].dispatch('click');
            renderFactors([translated], {{overview, tableBody, groupMetadata, locale: 'en'}});
            assert.equal(popover.hidden, true);
            assert.equal(buttons[0].getAttribute('aria-expanded'), 'false');
            assert.equal(byClass(body, 'factor-popover').length, 1);
            const rerendered = [...byClass(overview, 'factor-info'), ...byClass(tableBody, 'factor-info')];
            rerendered[0].dispatch('click');
            assert.match(treeText(popover), /Future factor/);
            assert.match(treeText(popover), /Current value 1.25x/);
            assert.match(treeText(popover), /Data date 2026-07-22/);
            assert.match(treeText(popover), /Version v2/);
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )

    def test_factor_popover_styles_are_viewport_safe_and_keyboard_visible(self):
        css = (STATIC / "css/dashboard.css").read_text()

        self.assertRegex(css, r"\.factor-popover\s*\{[^}]*position:\s*fixed")
        self.assertRegex(css, r"\.factor-popover\s*\{[^}]*max-width:\s*min\(")
        self.assertRegex(css, r"\.factor-popover\s*\{[^}]*overflow-y:\s*auto")
        self.assertIn(".factor-popover[hidden]", css)
        self.assertIn(".factor-info:focus-visible", css)

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
            "descending_trendline",
            "prior_high_breakout",
            "trendline_breakout",
            "higher_low_confirmed",
            "reversal_signal_count",
            "whitespaceSeriesPoints",
            "forecastProjectionSeries",
            "onForecastDate",
            "forecastRequestDelayMs",
        ):
            self.assertIn(marker, source)

        self.assertEqual(source.count("LightweightCharts.CandlestickSeries"), 1)
        self.assertEqual(source.count("LightweightCharts.HistogramSeries"), 1)
        self.assertEqual(source.count("LightweightCharts.LineSeries"), 7)
        for title_key in (
            "chart.pivot.strictVcp",
            "chart.pivot.tightPlatform",
            "chart.series.volumeMa20",
            "chart.series.volumeRatio",
        ):
            self.assertIn(title_key, source)
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
        self.assertIn("chartController?.setLocale(locale)", app_source)

        html = HTML.read_text()
        self.assertIn('data-range="3m"', html)
        self.assertIn('data-range="6m"', html)
        self.assertIn('data-range="1y"', html)
        self.assertIn('data-range="2y"', html)
        self.assertIn('data-range="all"', html)

    def test_chart_dates_are_deterministic(self):
        module_uri = (STATIC / "js/charts.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            const created = [];
            function node() {{
              return {{textContent: '', className: '', children: [],
                append(...items) {{ this.children.push(...items); }},
                replaceChildren(...items) {{ this.children = [...items]; this.textContent = ''; }}}};
            }}
            globalThis.document = {{ createElement: () => node() }};
            function chart(options) {{
              const scale = {{subscribeVisibleLogicalRangeChange() {{}},
                unsubscribeVisibleLogicalRangeChange() {{}}, setVisibleLogicalRange() {{}}, fitContent() {{}}}};
              const value = {{options, applied: [], timeScale: () => scale,
                addSeries(_type, seriesOptions) {{
                  return {{options: seriesOptions, setData() {{}}, createPriceLine() {{ return {{}}; }},
                    removePriceLine() {{}}, applyOptions() {{}}}};
                }}, subscribeCrosshairMove() {{}}, unsubscribeCrosshairMove() {{}},
                subscribeClick() {{}}, unsubscribeClick() {{}},
                applyOptions(next) {{ this.applied.push(next); }}, remove() {{}},
                setCrosshairPosition() {{}}, clearCrosshairPosition() {{}}}};
              created.push(value); return value;
            }}
            globalThis.LightweightCharts = {{
              CandlestickSeries: 'candles', HistogramSeries: 'histogram', LineSeries: 'line',
              CrosshairMode: {{Normal: 0}}, LineStyle: {{Dashed: 2}},
              createChart(_element, options) {{ return chart(options); }},
              createSeriesMarkers() {{ return {{setMarkers() {{}}}}; }},
            }};
            const {{ createLinkedCharts }} = await import({json.dumps(module_uri)});
            const detail = node();
            const controller = createLinkedCharts(
              {{clientWidth: 800, clientHeight: 400}},
              {{clientWidth: 800, clientHeight: 180}}, detail,
              {{locale: 'en'}},
            );
            controller.setChartData({{chart: [{{time: '2026-07-17', open: 1, high: 2, low: 0,
              close: 1.5, volume: 10}}]}});
            controller.setLocale('zh-CN');
            for (const value of created) {{
              assert.equal(value.options.timeScale.tickMarkFormatter('2026-07-17'), '07-17');
              assert.equal(value.options.localization.timeFormatter({{year: 2026, month: 7, day: 17}}),
                '2026-07-17');
              assert.deepEqual(value.applied.at(-1), {{
                timeScale: {{tickMarkFormatter: value.options.timeScale.tickMarkFormatter}},
                localization: {{timeFormatter: value.options.localization.timeFormatter}},
              }});
            }}
            assert.equal(detail.children[0].children[0].textContent, '2026-07-17');
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chart_locale_switch_preserves_locked_row_and_localizes_runtime_details(self):
        module_uri = (STATIC / "js/charts.js").as_uri()
        script = f"""
            import assert from 'node:assert/strict';
            const created = [];
            function node() {{
              return {{textContent: '', className: '', children: [],
                append(...items) {{ this.children.push(...items); }},
                replaceChildren(...items) {{ this.children = [...items]; this.textContent = ''; }}}};
            }}
            globalThis.document = {{ createElement: () => node() }};
            function chart(name) {{
              const scale = {{rangeHandler: null,
                subscribeVisibleLogicalRangeChange(handler) {{ this.rangeHandler = handler; }},
                unsubscribeVisibleLogicalRangeChange() {{ this.rangeHandler = null; }},
                setVisibleLogicalRange(range) {{
                  this.visibleLogicalRange = range;
                  this.rangeHandler?.(range);
                }}, fitContent() {{}}}};
              const value = {{name, series: [], clickHandler: null, crosshairHandler: null, crosshair: null,
                timeScale: () => scale,
                addSeries(type, options) {{
                  const series = {{type, options, applied: [], setData() {{}},
                    createPriceLine() {{ return {{}}; }}, removePriceLine() {{}},
                    applyOptions(next) {{ this.applied.push(next); }}}};
                  this.series.push(series); return series;
                }}, subscribeCrosshairMove(handler) {{ this.crosshairHandler = handler; }}, unsubscribeCrosshairMove() {{}},
                subscribeClick(handler) {{ this.clickHandler = handler; }}, unsubscribeClick() {{}},
                applyOptions() {{}}, remove() {{}},
                setCrosshairPosition(value, time, series) {{ this.crosshair = {{value, time, type: series.type}}; }},
                clearCrosshairPosition() {{ this.crosshair = null; }}}};
              created.push(value); return value;
            }}
            globalThis.LightweightCharts = {{
              CandlestickSeries: 'candles', HistogramSeries: 'histogram', LineSeries: 'line',
              CrosshairMode: {{Normal: 0}}, LineStyle: {{Dashed: 2}},
              createChart(element) {{ return chart(element.name); }},
              createSeriesMarkers() {{ return {{setMarkers() {{}}}}; }},
            }};
            const {{ createLinkedCharts }} = await import({json.dumps(module_uri)});
            const detail = node();
            const controller = createLinkedCharts(
              {{name: 'price', clientWidth: 800, clientHeight: 400}},
              {{name: 'volume', clientWidth: 800, clientHeight: 180}}, detail,
              {{locale: 'en'}},
            );
            const lockedRow = {{time: '2026-07-17', open: 10, high: 12, low: 9, close: 11,
              volume: 1000, volume_ma20: 900, volume_ratio: 1.11}};
            const latestRow = {{time: '2026-07-18', open: 11, high: 13, low: 10, close: 12,
              volume: 1200, volume_ma20: 1000, volume_ratio: 1.2}};
            const chartRows = Array.from({{length: 65}}, (_, index) => ({{
              ...latestRow,
              time: new Date(Date.UTC(2026, 6, 17 + index)).toISOString().slice(0, 10),
              close: latestRow.close + index,
            }}));
            chartRows[0] = lockedRow;
            controller.setChartData({{chart: chartRows}});
            controller.setRange('3m');
            assert.ok(created[0].timeScale().visibleLogicalRange.from > 0);
            assert.equal(created[0].timeScale().visibleLogicalRange.to, chartRows.length - 1 + 20);
            created[0].crosshairHandler({{time: lockedRow.time}});
            const visualState = {{
              priceRange: structuredClone(created[0].timeScale().visibleLogicalRange),
              volumeRange: structuredClone(created[1].timeScale().visibleLogicalRange),
              volumeCrosshair: structuredClone(created[1].crosshair),
            }};
            created[0].clickHandler({{time: lockedRow.time}});
            assert.equal(detail.children[0].children[0].textContent, lockedRow.time);
            assert.equal(detail.children[0].children[1].textContent, 'Locked · click a chart to unlock');

            controller.setLocale('zh-CN');
            assert.deepEqual(created[0].timeScale().visibleLogicalRange, visualState.priceRange);
            assert.deepEqual(created[1].timeScale().visibleLogicalRange, visualState.volumeRange);
            assert.deepEqual(created[1].crosshair, visualState.volumeCrosshair);
            assert.equal(detail.children[0].children[0].textContent, lockedRow.time);
            assert.equal(detail.children[0].children[1].textContent, '已锁定 · 点击图表解锁');
            const zhLabels = detail.children[1].children.map(item => item.children[0].textContent);
            assert.ok(zhLabels.includes('开盘价'));
            assert.ok(zhLabels.includes('成交量 MA20'));
            const zhVolumeTitles = created[1].series.filter(series => series.type === 'line')
              .map(series => series.applied.at(-1).title);
            assert.deepEqual(zhVolumeTitles, ['成交量 MA20', '成交量比率']);

            controller.setLocale('en');
            assert.deepEqual(created[0].timeScale().visibleLogicalRange, visualState.priceRange);
            assert.deepEqual(created[1].timeScale().visibleLogicalRange, visualState.volumeRange);
            assert.deepEqual(created[1].crosshair, visualState.volumeCrosshair);
            assert.equal(detail.children[0].children[0].textContent, lockedRow.time);
            assert.equal(detail.children[0].children[1].textContent, 'Locked · click a chart to unlock');
            const enLabels = detail.children[1].children.map(item => item.children[0].textContent);
            assert.ok(enLabels.includes('Open'));
            assert.ok(enLabels.includes('Volume MA20'));
            const enVolumeTitles = created[1].series.filter(series => series.type === 'line')
              .map(series => series.applied.at(-1).title);
            assert.deepEqual(enVolumeTitles, ['Volume MA20', 'Volume ratio']);
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chart_forecast_interaction(self):
        forecast_uri = (STATIC / "js/forecasts.js").as_uri()
        chart_uri = (STATIC / "js/charts.js").as_uri()
        script = rf"""
            import assert from 'node:assert/strict';
            const created = [];
            const markerControllers = [];
            function node() {{
              return {{textContent: '', className: '', children: [], dataset: {{}}, attributes: {{}},
                append(...items) {{ this.children.push(...items); }},
                replaceChildren(...items) {{ this.children = [...items]; this.textContent = ''; }},
                setAttribute(name, value) {{ this.attributes[name] = String(value); }},
                getAttribute(name) {{ return this.attributes[name]; }}}};
            }}
            function textTree(value) {{
              return [value.textContent, ...value.children.map(textTree)].join(' ')
                .replace(/\\s+/g, ' ').trim();
            }}
            globalThis.document = {{createElement: () => node()}};
            function chart(name) {{
              const scale = {{range: null, subscribeVisibleLogicalRangeChange() {{}},
                unsubscribeVisibleLogicalRangeChange() {{}},
                setVisibleLogicalRange(next) {{ this.range = next; }}, fitContent() {{}}}};
              const value = {{name, series: [], crosshairHandler: null, clickHandler: null,
                crosshairPositions: [], programmaticEvents: 0, forecastDataEvents: 0,
                timeScale: () => scale,
                addSeries(type, options) {{
                  const series = {{type, options, data: [], setData(data) {{
                    this.data = data;
                    if (options.title === '模型预测线' && value.crosshairHandler
                        && value.forecastDataEvents < 8) {{
                      value.forecastDataEvents += 1;
                      value.crosshairHandler({{time: '2026-07-17'}});
                    }}
                  }},
                    createPriceLine() {{ return {{}}; }}, removePriceLine() {{}}, applyOptions() {{}}}};
                  this.series.push(series); return series;
                }},
                subscribeCrosshairMove(handler) {{ this.crosshairHandler = handler; }},
                unsubscribeCrosshairMove() {{}}, subscribeClick(handler) {{ this.clickHandler = handler; }},
                unsubscribeClick() {{}},
                setCrosshairPosition(value, time, series) {{
                  this.crosshairPositions.push({{value, time, series}});
                  if (this.crosshairHandler && this.programmaticEvents < 8) {{
                    this.programmaticEvents += 1;
                    queueMicrotask(() => this.crosshairHandler({{}}));
                  }}
                }},
                clearCrosshairPosition() {{}},
                applyOptions() {{}}, remove() {{}}}};
              created.push(value); return value;
            }}
            globalThis.LightweightCharts = {{
              CandlestickSeries: 'candles', HistogramSeries: 'histogram', LineSeries: 'line',
              CrosshairMode: {{Normal: 0}}, LineStyle: {{Dashed: 2}},
              createChart(element) {{ return chart(element.name); }},
              createSeriesMarkers(_series, markers) {{
                const controller = {{markers, calls: 1, setMarkers(next) {{
                  this.markers = next; this.calls += 1;
                }}}};
                markerControllers.push(controller); return controller;
              }},
            }};
            const forecasts = await import({json.dumps(forecast_uri)});
            const payload = {{
              forecasts: {{model: {{key: 'ridge_direction_v1', version: 'ridge-v1'}},
                horizons: [5, 20, 60], date_coverage: {{requested_date_count: 70,
                  computed_date_count: 1, policy: 'latest_only_synchronous',
                  computed_dates: ['2026-07-17'], omitted_reason: 'not_precomputed'}}, by_date: {{
                  '2026-07-17': {{
                    '5': {{direction: 'neutral', predicted_return: 0.001, up_probability: null,
                      confidence_status: 'uncalibrated',
                      confidence_reason: 'insufficient_calibration_samples', training_sample_count: 108,
                      training_cutoff: '2026-07-09', model_key: 'ridge_direction_v1', model_version: 'ridge-v1'}},
                    '20': {{direction: 'up', predicted_return: 0.034, up_probability: 0.64,
                      confidence_status: 'calibrated', confidence_reason: null, training_sample_count: 105,
                      training_cutoff: '2026-06-19', model_key: 'ridge_direction_v1', model_version: 'ridge-v1'}},
                    '60': {{direction: 'down', predicted_return: -0.051, up_probability: null,
                      confidence_status: 'uncalibrated',
                      confidence_reason: 'calibration_requires_both_classes', training_sample_count: 101,
                      training_cutoff: '2026-04-24', model_key: 'ridge_direction_v1', model_version: 'ridge-v1'}},
                  }}
                }}}},
              forecast_evaluation: {{
                '5': {{sample_count: 90, coverage: 0.8, direction_accuracy: 0.55, mae: 0.015,
                  zero_return_mae: 0.018, historical_mean_mae: 0.017,
                  evaluation_start: '2025-01-03', evaluation_end: '2026-06-30', model_version: 'ridge-v1'}},
                '20': {{sample_count: 80, coverage: 0.75, direction_accuracy: 0.6, mae: 0.025,
                  zero_return_mae: 0.03, historical_mean_mae: 0.028,
                  evaluation_start: '2025-01-03', evaluation_end: '2026-06-30', model_version: 'ridge-v1'}},
                '60': {{sample_count: 0, unavailable_reason: 'not_precomputed',
                  model_version: 'ridge-v1'}},
              }},
            }};
            const index = forecasts.indexForecasts(payload);
            assert.equal(forecasts.forecastFor('2026-07-17', 20),
              payload.forecasts.by_date['2026-07-17']['20']);
            assert.equal(forecasts.forecastFor(index, {{year: 2026, month: 7, day: 17}}, '60'),
              payload.forecasts.by_date['2026-07-17']['60']);
            assert.equal(forecasts.forecastFor('2026-07-18', 20), null);
            const directIndex = forecasts.indexForecasts(payload.forecasts);
            assert.equal(forecasts.forecastFor(directIndex, '2026-07-17', 5),
              payload.forecasts.by_date['2026-07-17']['5']);

            const {{createLinkedCharts}} = await import({json.dumps(chart_uri)});
            const detail = node();
            const controller = createLinkedCharts(
              {{name: 'price', clientWidth: 800, clientHeight: 400}},
              {{name: 'volume', clientWidth: 800, clientHeight: 180}}, detail,
              {{locale: 'zh-CN'}},
            );
            const rows = Array.from({{length: 70}}, (_, index) => ({{
              time: new Date(Date.UTC(2026, 4, 10 + index)).toISOString().slice(0, 10),
              open: 100, high: 103, low: 99, close: 102, volume: 1000,
            }}));
            rows[68].time = '2026-07-17';
            rows[69].time = '2026-07-18';
            controller.setChartData({{...payload, chart: rows, structures: {{annotations: [{{
              time: '2026-07-18', type: 'strict_vcp', label: 'Strict VCP',
            }}]}}}});
            assert.equal(created[0].forecastDataEvents, 1);
            assert.equal(controller.getForecastHorizon(), 20);
            assert.equal(markerControllers.length, 1);

            created[0].crosshairHandler({{time: '2026-07-17'}});
            await new Promise((resolve) => setTimeout(resolve, 0));
            assert.equal(
              created[0].crosshairPositions.length + created[1].crosshairPositions.length,
              1,
            );
            const forecastMarkers = markerControllers[0];
            assert.deepEqual(forecastMarkers.markers[0], {{time: '2026-07-17', position: 'belowBar',
              color: '#35c6a5', shape: 'arrowUp', text: '预测方向：上涨'}});
            assert.equal(forecastMarkers.markers[1].time, '2026-07-18');
            const zhUp = textTree(detail);
            assert.match(zhUp, /上涨概率 64\.0%/);
            assert.match(zhUp, /预测收益率 \+3\.40%/);
            assert.match(zhUp, /训练样本 105/);
            assert.match(zhUp, /训练截止日期 2026-06-19/);
            assert.match(zhUp, /模型 ridge_direction_v1 · ridge-v1/);
            assert.match(zhUp, /滚动前推历史证据/);
            assert.match(zhUp, /覆盖率 75\.0%/);
            assert.match(zhUp, /方向准确率 60\.0%/);
            assert.match(zhUp, /平均绝对误差 2\.50%/);
            assert.match(zhUp, /基线比较/);
            assert.match(zhUp, /样本期 2025-01-03 — 2026-06-30/);
            assert.match(zhUp, /仅供研究/);

            const callsBeforeSwitch = forecastMarkers.calls;
            assert.equal(controller.setForecastHorizon(5), 5);
            assert.equal(controller.getForecastHorizon(), 5);
            assert.deepEqual(forecastMarkers.markers[0], {{time: '2026-07-17', position: 'aboveBar',
              color: '#91a3b0', shape: 'circle', text: '预测方向：中性'}});
            const zhNeutral = textTree(detail);
            assert.doesNotMatch(zhNeutral, /上涨概率/);
            assert.match(zhNeutral, /置信度说明 校准样本不足/);

            assert.equal(controller.setForecastHorizon(60), 60);
            assert.deepEqual(forecastMarkers.markers[0], {{time: '2026-07-17', position: 'aboveBar',
              color: '#ff7a7a', shape: 'arrowDown', text: '预测方向：下跌'}});
            assert.equal(markerControllers.length, 1);
            assert.ok(forecastMarkers.calls > callsBeforeSwitch);

            created[0].clickHandler({{time: '2026-07-17'}});
            created[0].crosshairHandler({{time: '2026-07-18'}});
            assert.equal(forecastMarkers.markers[0].time, '2026-07-17');
            assert.match(textTree(detail), /已锁定/);
            const lockedPositionsBeforeSwitch = created[0].crosshairPositions.length;
            controller.setForecastHorizon(20);
            assert.equal(created[0].crosshairPositions.length, lockedPositionsBeforeSwitch + 1);
            assert.deepEqual(created[0].crosshairPositions.at(-1),
              {{value: 102, time: '2026-07-17', series: created[0].series[0]}});
            controller.setForecastHorizon(60);
            controller.setRange('3m');
            controller.setLocale('en');
            assert.equal(controller.getForecastHorizon(), 60);
            assert.equal(forecastMarkers.markers[0].time, '2026-07-17');
            assert.equal(forecastMarkers.markers[0].text, 'Forecast direction: Down');
            assert.match(textTree(detail), /Locked/);
            assert.match(textTree(detail), /Confidence note Both outcome classes are required/);
            assert.match(textTree(detail), /Evidence status Not precomputed/);

            created[0].clickHandler({{time: '2026-07-18'}});
            assert.equal(forecastMarkers.markers.length, 1);
            assert.equal(forecastMarkers.markers[0].time, '2026-07-18');
            assert.doesNotMatch(forecastMarkers.markers[0].text, /Forecast direction/);
            assert.match(textTree(detail), /Unavailable/);
            assert.match(textTree(detail), /Unavailable reason Historical point not precomputed/);

            const failureDetail = node();
            forecasts.renderForecastDetail(failureDetail, {{locale: 'en', horizon: 20,
              date: '2026-07-17', forecast: null,
              model: {{unavailable_reason: 'insufficient_training_samples'}},
              dateCoverage: {{computed_dates: ['2026-07-17'], omitted_reason: 'not_precomputed'}},
              evaluation: payload.forecast_evaluation['20']}});
            assert.match(textTree(failureDetail), /Unavailable reason Insufficient training samples/);

            const mixedZh = node();
            forecasts.renderForecastDetail(mixedZh, {{locale: 'zh-CN', horizon: 20,
              date: '2026-07-17', forecast: null,
              model: {{unavailable_reason: 'no_available_forecasts'}},
              dateCoverage: {{computed_dates: ['2026-07-17'], omitted_reason: 'not_precomputed'}},
              evaluation: payload.forecast_evaluation['20']}});
            assert.match(textTree(mixedZh), /不可用原因 无可用预测/);

            const mixedEn = node();
            forecasts.renderForecastDetail(mixedEn, {{locale: 'en', horizon: 20,
              date: '2026-07-17', forecast: null,
              model: {{unavailable_reason: 'no_available_forecasts'}},
              dateCoverage: {{computed_dates: ['2026-07-17'], omitted_reason: 'not_precomputed'}},
              evaluation: payload.forecast_evaluation['20']}});
            assert.match(textTree(mixedEn), /Unavailable reason No available forecasts/);
            assert.equal(controller.setForecastHorizon(40), 60);
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        html = HTML.read_text()
        self.assertEqual(len(re.findall(r'data-forecast-horizon="(?:5|20|60)"', html)), 3)
        self.assertIn('data-forecast-horizon="20" aria-pressed="true"', html)
        self.assertNotIn('data-forecast-horizon="40"', html)
        self.assertIn('data-i18n="forecast.disclaimer"', html)
        css = (STATIC / "css/dashboard.css").read_text()
        self.assertIn(".forecast-controls", css)
        self.assertIn(".forecast-evidence", css)
        self.assertIn(".quiet-button, .range-controls button, .forecast-controls button", css)
        self.assertNotIn("var(--up)", css)
        self.assertNotIn("var(--down)", css)

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
            const requestedForecastDates = [];
            const controller = createLinkedCharts(
              {{name: 'price', clientWidth: 800, clientHeight: 400}},
              {{name: 'volume', clientWidth: 800, clientHeight: 180}}, detail,
              {{locale: 'en', forecastRequestDelayMs: 0, onForecastDate: async (date) => {{
                requestedForecastDates.push(date);
                return {{forecasts: {{model: {{key: 'ridge', version: 'v2'}},
                  date_coverage: {{computed_dates: [date]}},
                  by_date: {{[date]: {{'20': {{direction: 'up', predicted_return: 0.1,
                    target_date: '2026-08-19',
                    projection_dates: ['2026-07-23', '2026-07-24', '2026-07-27',
                      '2026-07-28', '2026-07-29', '2026-07-30', '2026-07-31',
                      '2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06',
                      '2026-08-07', '2026-08-10', '2026-08-11', '2026-08-12',
                      '2026-08-13', '2026-08-14', '2026-08-17', '2026-08-18',
                      '2026-08-19'],
                    model_key: 'ridge', model_version: 'v2'}}}}}}}}}};
              }}}},
            );
            controller.setChartData({{chart: []}});
            const emptyDetail = detail.textContent;
            const row = {{time: '2026-07-22', open: 99, high: 102, low: 98, close: 101,
              volume: 1200, volume_ma20: 1000, volume_ratio: 1.2, volume_ratio_change: 0.15,
              ema20: 100, sma50: 95, sma200: 90, daily_return: 0.01, true_range_pct: 4,
              volume_change: 0.1, atr20: 3, pivot: 100, pivot_distance_pct: 1,
              pivot_distance_change_pct: 0.75, ema20_cross: 'above', sma50_cross: null,
              prior_high_resistance: 100, prior_high_breakout_pct: 1,
              prior_high_breakout: true, descending_trendline: 100.5,
              trendline_breakout: true, higher_low_confirmed: false,
              reversal_signal_count: 2, reversal_candidate: true,
              trendline_high_1_date: '2026-07-01', trendline_high_2_date: '2026-07-15',
              latest_confirmed_high_date: '2026-07-15',
              latest_confirmed_high_confirmed_date: '2026-07-18',
              higher_low_previous_date: '2026-06-10', higher_low_previous_price: 90,
              higher_low_latest_date: '2026-07-10', higher_low_latest_price: 94,
              higher_low_confirmation_date: '2026-07-14'}};
            controller.setChartData({{chart: [row], structures: {{key_levels: {{
              strict_vcp_pivot: 103, tight_platform_pivot: 104,
            }}, annotations: [{{time: row.time, type: 'strict_vcp', label: 'Strict VCP'}}]}}}});
            await new Promise((resolve) => setTimeout(resolve, 0));
            await Promise.resolve();
            console.log(JSON.stringify({{
              priceLines: created[0].priceLines.map(line => line.title),
              volumeLines: created[1].series.filter(series => series.type === 'line')
                .map(series => [series.options.title, series.data]),
              priceLineSeries: created[0].series.filter(series => series.type === 'line')
                .map(series => [series.options.title, series.data]),
              markers: markerSets.at(-1),
              requestedForecastDates,
              emptyDetail,
              detail: chartModule.detailItems
                ? chartModule.detailItems(row, 'en').map(item => [item.label, item.value]) : [],
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
        self.assertEqual(actual["emptyDetail"], "No chart observations are available.")
        self.assertEqual(actual["priceLines"], ["Strict VCP pivot", "Tight-platform pivot"])
        self.assertEqual(
            [line[0] for line in actual["volumeLines"]],
            ["Volume MA20", "Volume ratio"],
        )
        self.assertEqual(actual["markers"][0]["text"], "Strict VCP")
        self.assertEqual(actual["requestedForecastDates"], ["2026-07-22"])
        self.assertIn("Prior-high breakout", [marker["text"] for marker in actual["markers"]])
        self.assertIn("Trendline breakout", [marker["text"] for marker in actual["markers"]])
        self.assertIn("Reversal candidate 2/3", [marker["text"] for marker in actual["markers"]])
        self.assertEqual(
            next(line for line in actual["priceLineSeries"] if line[0] == "Descending resistance"),
            ["Descending resistance", [{"time": "2026-07-22", "value": 100.5}]],
        )
        projection = next(line for line in actual["priceLineSeries"] if line[0] == "Model forecast")
        self.assertEqual(projection[1][0], {"time": "2026-07-22", "value": 101})
        self.assertEqual(len(projection[1]), 21)
        self.assertAlmostEqual(projection[1][1]["value"], 101.505)
        self.assertEqual(projection[1][1]["time"], "2026-07-23")
        self.assertAlmostEqual(projection[1][10]["value"], 106.05)
        self.assertAlmostEqual(projection[1][-1]["value"], 111.1)
        details = dict(actual["detail"])
        self.assertEqual(details["Volume ratio change"], "+0.15×")
        self.assertEqual(details["Pivot-distance change"], "+0.75 pp")
        self.assertEqual(details["EMA20 cross"], "Crossed above")
        self.assertEqual(details["Prior-high breakout"], "Yes")
        self.assertEqual(details["Trendline breakout"], "Yes")
        self.assertEqual(details["Reversal conditions"], "2/3")
        self.assertEqual(details["Latest high confirmed"], "2026-07-18")
        self.assertEqual(details["Higher-low confirmation"], "2026-07-14")
        self.assertEqual(details["Higher-low pivots"], "2026-06-10 90 → 2026-07-10 94")

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

    def test_price_and_volume_charts_reserve_space_for_the_date_axis(self):
        css = (STATIC / "css/dashboard.css").read_text()

        volume = re.search(r"\.volume-placeholder\s*\{([^}]*)\}", css)
        self.assertIsNotNone(volume)
        gap = re.search(r"margin-top:\s*(\d+)px", volume.group(1))
        self.assertIsNotNone(gap)
        self.assertGreaterEqual(
            int(gap.group(1)),
            20,
            "The lower chart must not visually cover the price chart's date labels.",
        )

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
            const groups = groupFactorResults(factors, metadata, 'en');
            const rows = factorDetailRows(factors, 'en');
            const overview = factorModule.overviewFactorGroups
              ? factorModule.overviewFactorGroups(factors, metadata, 'en') : [];
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
                    [
                        "20 sessions",
                        "12 non-overlapping samples",
                        "12 non-overlapping 20-session historical returns, with absolute quantiles capped at three times current 63-session realized-volatility scaling.",
                    ],
                    ["40 sessions", "4 non-overlapping samples", "insufficient samples"],
                ],
                "methodology": "Descriptive historical scenarios from non-overlapping horizon returns available at the observation date; not predictions or probabilities.",
            },
        )

    def test_update_controller_polls_running_jobs_and_exposes_429_resume(self):
        module_uri = (STATIC / "js/update.js").as_uri()
        i18n_uri = (STATIC / "js/i18n.js").as_uri()
        script = f"""
            import {{ setLocale }} from {json.dumps(i18n_uri)};
            import {{
              createUpdateController, shouldReloadSelectedTicker,
              shouldReloadAfterUpdate, updateRetryDelay
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
              reloadAfterUpdate: [
                shouldReloadAfterUpdate({{updated: 1}}, false, false),
                shouldReloadAfterUpdate({{updated: 0}}, false, false),
                shouldReloadAfterUpdate({{updated: 0}}, false, true),
              ],
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
        self.assertEqual(actual["reloadAfterUpdate"], [True, False, True])
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
