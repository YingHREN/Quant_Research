import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const moduleUrl = process.argv[2] || pathToFileURL(
  new URL(
    "../web/static/js/policy-period-chart.mjs",
    import.meta.url,
  ).pathname,
).href;
const {
  createPolicyPeriodChart,
  periodForTime,
  policyBandSegments,
} = await import(moduleUrl);

const rows = [
  { time: "2020-01-02", close: 100, normalized: 100 },
  { time: "2020-03-16", close: 90, normalized: 90 },
  { time: "2022-03-16", close: 130, normalized: 130 },
  { time: "2022-03-17", close: 131, normalized: 131 },
  { time: "2024-09-18", close: 160, normalized: 160 },
  { time: "2024-09-19", close: 161, normalized: 161 },
  { time: "2026-07-29", close: 190, normalized: 190 },
];
const periods = [
  {
    period_id: "easing",
    label_zh: "宽松",
    label_en: "Easing",
    start_date: "2020-03-15",
    end_date: "2022-03-16",
    available_at: "2020-03-15T20:00:00+00:00",
    is_complete: true,
  },
  {
    period_id: "tightening",
    label_zh: "紧缩",
    label_en: "Tightening",
    start_date: "2022-03-17",
    end_date: "2024-09-18",
    available_at: "2022-03-16T20:00:00+00:00",
    is_complete: true,
  },
  {
    period_id: "open",
    label_zh: "进行中",
    label_en: "Ongoing",
    start_date: "2024-09-19",
    end_date: null,
    available_at: "2024-09-18T20:00:00+00:00",
    is_complete: false,
  },
  {
    period_id: "future",
    label_zh: "未来",
    label_en: "Future",
    start_date: "2027-01-01",
    end_date: null,
    available_at: "2027-01-01T20:00:00+00:00",
    is_complete: false,
  },
];

const segments = policyBandSegments(
  periods,
  rows,
  "2026-07-29T23:59:59+00:00",
);
assert.deepEqual(
  segments.map((row) => [
    row.period_id,
    row.start_time,
    row.end_time,
  ]),
  [
    ["easing", "2020-03-16", "2022-03-16"],
    ["tightening", "2022-03-17", "2024-09-18"],
    ["open", "2024-09-19", "2026-07-29"],
  ],
);
assert.equal(
  periodForTime(periods, "2023-01-03", "2026-07-29").period_id,
  "tightening",
);
assert.equal(
  periodForTime(periods, "2027-01-03", "2026-07-29"),
  null,
);

class TestNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.clientWidth = 900;
    this._text = "";
  }

  set textContent(value) {
    this._text = value == null ? "" : String(value);
    this.children = [];
  }

  get textContent() {
    return this._text
      + this.children.map((child) => child.textContent).join("");
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
    this._text = "";
  }
}

const documentStub = {
  createElement: (tagName) => new TestNode(tagName),
};
const charts = [];
const chartLibrary = {
  LineSeries: "line",
  CrosshairMode: { Normal: "normal" },
  createChart(element, options) {
    const chart = {
      element,
      options,
      series: [],
      clickHandler: null,
      rangeHandler: null,
      removed: false,
      addSeries(type, seriesOptions) {
        const series = {
          type,
          options: seriesOptions,
          data: [],
          setData(next) { this.data = next; },
          applyOptions(next) {
            this.options = { ...this.options, ...next };
          },
        };
        this.series.push(series);
        return series;
      },
      applyOptions(next) {
        this.options = { ...this.options, ...next };
      },
      subscribeClick(handler) { this.clickHandler = handler; },
      timeScale() {
        return {
          fitContent() {},
          subscribeVisibleLogicalRangeChange: (handler) => {
            chart.rangeHandler = handler;
          },
          timeToCoordinate: (time) => (
            rows.findIndex((row) => row.time === time) * 100
          ),
        };
      },
      remove() { this.removed = true; },
    };
    charts.push(chart);
    return chart;
  },
};
globalThis.LightweightCharts = chartLibrary;
globalThis.document = documentStub;

const chartElement = new TestNode("div");
const overlayElement = new TestNode("div");
const selected = [];
const translate = (key) => ({
  "market.policyChart.price": "Adjusted close",
  "market.policyMatrix.period.incomplete": "In progress",
}[key] || key);
const controller = createPolicyPeriodChart({
  chartElement,
  overlayElement,
  translate,
  locale: "en",
  onPeriodSelect: (periodId) => selected.push(periodId),
});
controller.update(
  {
    benchmark: "SPY",
    asof: "2026-07-29T23:59:59+00:00",
    rows,
  },
  periods,
);

assert.equal(charts.length, 1);
assert.equal(charts[0].options.height, 320);
assert.equal(charts[0].series.length, 1);
assert.equal(charts[0].series[0].data.length, rows.length);
assert.equal(overlayElement.children.length, 3);
assert.match(overlayElement.textContent, /Easing/);
assert.match(overlayElement.textContent, /In progress/);

charts[0].clickHandler({ time: "2023-01-03" });
assert.deepEqual(selected, ["tightening"]);

controller.update(
  {
    benchmark: "QQQ",
    asof: "2026-07-29T23:59:59+00:00",
    rows: rows.map((row) => ({ ...row, close: row.close * 2 })),
  },
  periods,
);
assert.equal(charts.length, 1);
assert.equal(charts[0].series.length, 1);
assert.equal(charts[0].series[0].data.at(-1).value, 380);

controller.destroy();
assert.equal(charts[0].removed, true);
