import assert from "node:assert/strict";

const [moduleUri] = process.argv.slice(2);

class Element {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.open = false;
    this._textContent = "";
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = value == null ? "" : String(value);
    this.children = [];
  }

  append(...items) {
    this.children.push(...items);
  }

  replaceChildren(...items) {
    this._textContent = "";
    this.children = [...items];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

function textTree(node) {
  return [node.textContent, ...node.children.map(textTree)]
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function descendants(node) {
  return node.children.flatMap((child) => [child, ...descendants(child)]);
}

globalThis.document = {
  createElement(tagName) {
    return new Element(tagName);
  },
};
globalThis.localStorage = {
  getItem() {
    return null;
  },
  setItem() {},
};

const { renderModelOutputs } = await import(moduleUri);
const container = new Element("div");
const identity = (key, nameKey, kind, status = "active") => ({
  key,
  version: "v1",
  kind,
  lifecycle: "production",
  status,
  timing: "close_confirmed",
  name_key: nameKey,
  explanation_key: `${nameKey.replace(/\.name$/, "")}.explanation`,
  limitation_key: `${nameKey.replace(/\.name$/, "")}.limitation`,
});
const forecast = {
  model_outputs: {
    primary: [{
      ...identity("ridge_direction_v1", "model.ridge.name", "statistical_forecast", "available"),
      horizon_sessions: 20,
      predicted_return: 0.1269,
      direction: "up",
      evidence_status: "unproven",
      training_sample_count: 90076,
      training_cutoff: "2026-06-30",
      direction_accuracy: 0.48,
      always_up_direction_accuracy: 0.61,
      balanced_accuracy: 0.46,
      non_overlapping_sample_count: 120,
      non_overlapping_direction_accuracy: 0.47,
    }],
    downside: [{
      ...identity("bearish_turn_immediate_v1", "model.immediateRisk.name", "rule_score"),
      score: 80,
      threshold: 70,
      conditions: ["distribution_day", "break_below_ema20"],
    }, {
      ...identity("macro_risk", "model.macroRisk.name", "remembered_state", "unavailable"),
      lifecycle: "planned",
      unavailable_reason: "not_implemented",
    }],
    bullish_structure: [{
      ...identity("bullish_structure_reversal_v1", "model.structuralReversal.name", "rule_score"),
      score: 2,
      maximum_score: 3,
      conditions: ["prior_high_breakout", "higher_low_confirmed"],
    }],
    decision: {
      ...identity("forecast_decision_policy", "model.decisionPolicy.name", "decision_policy", "available"),
      final_direction: "down",
      risk_state: "veto",
      action: "risk_override",
      reasons: ["bearish_turn_veto"],
    },
  },
};

renderModelOutputs(container, {
  forecast,
  date: "2026-07-01",
  locale: "zh-CN",
});
const zh = textTree(container);
const cards = descendants(container).filter((node) => node.dataset.modelCard);
assert.equal(container.dataset.state, "available");
assert.equal(cards.length, 5);
assert.match(zh, /2026-07-01/);
assert.match(zh, /Ridge/);
assert.match(zh, /最终方向/);
assert.match(zh, /下跌/);
assert.match(zh, /规则分数，不是概率/);
assert.match(zh, /计划中/);
assert.match(zh, /90,076/);
assert.match(zh, /始终上涨基线 \+61/);
assert.match(zh, /阈值 70/);

renderModelOutputs(container, {
  forecast,
  date: "2026-07-01",
  locale: "en",
});
const en = textTree(container);
assert.match(en, /Final direction/);
assert.match(en, /Rule score, not a probability/);
assert.match(en, /Planned/);

renderModelOutputs(container, {
  forecast: null,
  date: "2026-06-30",
  locale: "zh-CN",
  requestState: "loading",
});
const loading = textTree(container);
assert.equal(container.dataset.state, "loading");
assert.match(loading, /2026-06-30/);
assert.match(loading, /正在加载该日期的模型输出/);
assert.ok(container.children.length > 0);

console.log(JSON.stringify({ zh, en, loading, cardCount: cards.length }));
