import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const moduleUrl = process.argv[2] || pathToFileURL(
  new URL("../web/static/js/policy-period-matrix.mjs", import.meta.url).pathname,
).href;
const { renderPolicyPeriodMatrixView } = await import(moduleUrl);

class TestNode {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.className = "";
    this._text = "";
  }

  set textContent(value) {
    this._text = value == null ? "" : String(value);
    this.children = [];
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
    this._text = "";
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

const documentStub = {
  createElement: (tagName) => new TestNode(tagName),
};
const root = new TestNode("div");
const detail = new TestNode("div");
const translations = {
  "market.policyMatrix.symbol": "ETF",
  "market.policyMatrix.status.incomplete": "In progress",
  "market.policyMatrix.status.not_listed": "Not listed",
  "market.policyMatrix.status.missing_history": "Missing history",
  "market.policyMatrix.status.insufficient_history": "Insufficient history",
  "market.policyMatrix.status.unavailable_at_asof": "Unavailable at date",
  "market.policyMatrix.descriptionOnly": "Historical description",
  "market.policyMatrix.period.complete": "Complete",
  "market.policyMatrix.period.incomplete": "In progress",
  "market.policyMatrix.periodDates": "{start} to {end}",
  "market.policyMatrix.sourceTitle": "Official events",
  "market.policyMatrix.noEvents": "No linked events",
  "market.policyMatrix.authority": "Research · advisory · no authority",
  "market.unavailable.policy_catalog_unavailable": "Policy catalog unavailable",
};
const translate = (key, values = {}) => {
  let result = translations[key] || key;
  for (const [name, value] of Object.entries(values)) {
    result = result.replace(`{${name}}`, value);
  }
  return result;
};

const payload = {
  periods: [
    {
      period_id: "complete",
      label_en: "Tightening",
      label_zh: "紧缩",
      start_date: "2022-03-17",
      end_date: "2024-09-18",
      is_complete: true,
      interpretation_en: "Historical description only.",
      interpretation_zh: "仅用于历史描述。",
      events: [
        {
          event_id: "rate-a",
          effective_date: "2022-03-17",
          source_title: "Official rate decision",
          source_url: "https://www.federalreserve.gov/example",
        },
      ],
    },
    {
      period_id: "open",
      label_en: "Ongoing",
      label_zh: "进行中",
      start_date: "2024-09-19",
      end_date: null,
      is_complete: false,
      interpretation_en: "Still ongoing.",
      interpretation_zh: "仍在进行。",
      events: [],
    },
  ],
  rows: [
    {
      period_id: "complete",
      ticker: "XLK",
      status: "complete",
      total_return: 0.10,
      max_drawdown: -0.12,
    },
    {
      period_id: "open",
      ticker: "XLK",
      status: "incomplete",
      total_return: null,
      max_drawdown: null,
    },
  ],
  unavailable_reason: null,
  lifecycle: "research",
  decision_permission: "advisory",
  online_authority: "none",
};

renderPolicyPeriodMatrixView({
  document: documentStub,
  root,
  detail,
  payload,
  metric: "total_return",
  locale: "en",
  translate,
});
assert.match(root.textContent, /XLK/);
assert.match(root.textContent, /10.0%/);
assert.match(root.textContent, /In progress/);
assert.match(detail.textContent, /2022-03-17/);
assert.match(detail.textContent, /Historical description/);
assert.match(detail.textContent, /Official rate decision/);

renderPolicyPeriodMatrixView({
  document: documentStub,
  root,
  detail,
  payload,
  metric: "max_drawdown",
  locale: "en",
  translate,
});
assert.match(root.textContent, /-12.0%/);

renderPolicyPeriodMatrixView({
  document: documentStub,
  root,
  detail,
  payload: {
    ...payload,
    periods: [],
    rows: [],
    unavailable_reason: "policy_catalog_unavailable",
  },
  metric: "total_return",
  locale: "en",
  translate,
});
assert.match(root.textContent, /Policy catalog unavailable/);
assert.doesNotMatch(root.textContent, /0.0%/);
