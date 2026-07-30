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
  decision_permission: "advisory",
  name_key: nameKey,
  explanation_key: `${nameKey.replace(/\.name$/, "")}.explanation`,
  limitation_key: `${nameKey.replace(/\.name$/, "")}.limitation`,
});
const forecast = {
  feature_provenance: {
    registry_ref: "feature_provenance_registry_v1",
    feature_version: "ridge-features-v2",
    observed_through: "2026-07-01",
    source_cutoff: "2026-07-01",
    available_at: "2026-07-01T16:00:00-04:00",
    data_version: "abcdef0123456789".repeat(4),
    execution_timing: "next_session_open",
  },
  model_outputs: {
    registry: {
      version: "model_output_registry_v3",
      groups: [
        {
          key: "primary",
          label_key: "modelOutput.group.primary",
          order: 10,
          cardinality: "many",
        },
        {
          key: "downside",
          label_key: "modelOutput.group.downside",
          order: 20,
          cardinality: "many",
        },
        {
          key: "bullish_structure",
          label_key: "modelOutput.group.bullish",
          order: 30,
          cardinality: "many",
        },
        {
          key: "macro_context",
          label_key: "modelOutput.group.macro",
          order: 35,
          cardinality: "many",
        },
        {
          key: "decision",
          label_key: "modelOutput.group.decision",
          order: 40,
          cardinality: "single",
        },
      ],
      models: [],
    },
    primary: [{
      ...identity("ridge_direction_v1", "model.ridge.name", "statistical_forecast", "available"),
      horizon_sessions: 20,
      predicted_return: 0.1269,
      direction: "up",
      direction_reliability: "unproven",
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
      ...identity("high_level_distribution_risk_v1", "model.highLevelDistribution.name", "remembered_state"),
      score: 72,
      state: "confirmed",
      memory_age_sessions: 0,
      high_level_context_score: 75,
      distribution_pressure_score: 70,
      structure_damage_score: 55,
      conditions: ["distribution_day", "failed_breakout", "below_ema20"],
      distribution_count_5: 1,
      distribution_count_10: 3,
      distribution_count_20: 5,
      churning_count_10: 2,
      churning_cluster: true,
      climax_run_score: 80,
      climax_run_candidate: true,
      risk_recovery: true,
      conditions: ["distribution_day", "failed_breakout", "below_ema20", "strong_reclaim"],
    }, {
      ...identity("supply_pressure_v1", "model.supplyPressure.name", "rule_score"),
      score: 62,
      maximum_score: 100,
      threshold: 50,
      coverage: 0.92,
      supply_demand_state: "two_way_contest",
      conditions: ["distribution_day", "failed_breakout"],
      metrics: [
        {label_key: "modelOutput.metric.closeVolumeSupply", value: 30, format: "score"},
        {label_key: "modelOutput.metric.rejectionSupply", value: 17, format: "score"},
        {label_key: "modelOutput.metric.structureContextSupply", value: 15, format: "score"},
      ],
    }],
    bullish_structure: [{
      ...identity("bullish_structure_reversal_v1", "model.structuralReversal.name", "rule_score"),
      score: 2,
      maximum_score: 3,
      conditions: ["prior_high_breakout", "higher_low_confirmed"],
    }, {
      ...identity("strict_vcp", "model.strictVcp.name", "shape_state", "inactive"),
      unavailable_reason: "contractions_not_decreasing",
      metrics: [
        {label_key: "modelOutput.metric.pivot", value: 103.5, format: "number"},
        {label_key: "modelOutput.metric.contractions", value: 2, format: "number"},
      ],
    }, {
      ...identity("vcp_breakout_confirmed_v1", "model.vcpBreakout.name", "rule_event"),
      metrics: [
        {label_key: "modelOutput.metric.volumeRatio", value: 1.62, format: "ratio"},
        {label_key: "modelOutput.metric.requiredVolumeRatio", value: 1.4, format: "ratio"},
        {label_key: "modelOutput.metric.pctOverPivot", value: 2.1, format: "percent"},
      ],
    }, {
      ...identity("pocket_pivot_v1", "model.pocketPivot.name", "rule_event", "unavailable"),
      unavailable_reason: "insufficient_history",
      metrics: [
        {label_key: "modelOutput.metric.currentVolume", value: 1250000, format: "volume"},
      ],
    }, {
      ...identity("demand_confirmation_v1", "model.demandConfirmation.name", "rule_score"),
      score: 71,
      maximum_score: 100,
      threshold: 50,
      coverage: 0.92,
      supply_demand_state: "two_way_contest",
      conditions: ["buyer_absorption", "breakout_acceptance"],
      metrics: [
        {label_key: "modelOutput.metric.demandParticipation", value: 27, format: "score"},
        {label_key: "modelOutput.metric.demandAbsorption", value: 29, format: "score"},
        {label_key: "modelOutput.metric.breakoutContextDemand", value: 15, format: "score"},
      ],
    }],
    macro_context: [{
      ...identity("macro_risk_v1", "model.macroRisk.name", "rule_score"),
      score: 68,
      maximum_score: 100,
      coverage: 0.9,
      state: "high",
      conditions: ["two_year_yield_high", "high_yield_spread_stressed"],
      metrics: [
        {label_key: "modelOutput.metric.macro.rates", value: 80, format: "score"},
        {label_key: "modelOutput.metric.macro.credit_liquidity", value: 100, format: "score"},
      ],
    }],
    decision: {
      ...identity("forecast_decision_policy", "model.decisionPolicy.name", "decision_policy", "available"),
      decision_permission: "final_policy",
      final_direction: "down",
      primary_direction_reliability: "unproven",
      risk_state: "veto",
      action: "risk_override",
      reasons: ["bearish_turn_veto"],
    },
  },
};

const externalRegistry = forecast.model_outputs.registry;
delete forecast.model_outputs.registry;
forecast.model_outputs.registry_ref = externalRegistry.version;
renderModelOutputs(container, {
  forecast,
  date: "2026-07-01",
  locale: "zh-CN",
  registry: externalRegistry,
  featureRegistry: {
    version: "feature_provenance_registry_v1",
    feature_version: "ridge-features-v2",
    features: [],
  },
});
const zh = textTree(container);
const cards = descendants(container).filter((node) => node.dataset.modelCard);
assert.equal(container.dataset.state, "available");
assert.equal(cards.length, 11);
assert.match(zh, /2026-07-01/);
assert.match(zh, /Ridge/);
assert.match(zh, /方向尚未证明超过基线/);
assert.match(zh, /主预测可靠性/);
assert.match(zh, /最终方向/);
assert.match(zh, /下跌/);
assert.match(zh, /规则分数，不是概率/);
assert.match(zh, /90,076/);
assert.match(zh, /始终上涨基线 \+61/);
assert.match(zh, /阈值 70/);
assert.match(zh, /高位派发与顶部向下转折风险/);
assert.match(zh, /高位背景 75/);
assert.match(zh, /供应聚集 70/);
assert.match(zh, /结构破坏 55/);
assert.match(zh, /疑似派发代理，不代表已确认机构交易/);
assert.match(zh, /5日派发次数 1/);
assert.match(zh, /10日派发次数 3/);
assert.match(zh, /20日派发次数 5/);
assert.match(zh, /10日 Churning 次数 2/);
assert.match(zh, /末端加速分数 80/);
assert.match(zh, /强势收复并解除顶部风险记忆/);
assert.match(zh, /向上突破确认（VCP）/);
assert.match(zh, /成交量比率 1\.62×/);
assert.match(zh, /至少需要 1\.4×/);
assert.match(zh, /高于枢轴 2\.1%/);
assert.match(zh, /Pocket Pivot 需求确认/);
assert.match(zh, /历史数据不足/);
assert.match(zh, /收缩幅度未递减/);
assert.match(zh, /当前成交量 1\.25M/);
assert.match(zh, /供给压力代理/);
assert.match(zh, /需求确认代理/);
assert.match(zh, /证据覆盖率 92/);
assert.match(zh, /供需状态 多空争夺/);
assert.match(zh, /供给压力代理[^]*阈值 50/);
assert.match(zh, /收盘与成交量供给 30/);
assert.match(zh, /需求参与度 27/);
assert.match(zh, /宏观环境/);
assert.match(zh, /决策权限/);
assert.match(zh, /数据时点/);
assert.match(zh, /2026-07-01T16:00:00-04:00/);
assert.match(zh, /ridge-features-v2/);
assert.match(zh, /abcdef012345/);
assert.match(zh, /收盘确认，下一交易日开盘执行/);

renderModelOutputs(container, {
  forecast,
  date: "2026-07-01",
  locale: "en",
  registry: externalRegistry,
});
const en = textTree(container);
assert.match(en, /Final direction/);
assert.match(en, /Direction not proven above baselines/);
assert.match(en, /Primary forecast reliability/);
assert.match(en, /Rule score, not a probability/);
assert.match(en, /High-level distribution and bearish top-turn risk/);
assert.match(en, /Suspected distribution proxy, not verified institutional trading/);
assert.match(en, /VCP breakout confirmation/);
assert.match(en, /Volume ratio 1\.62×/);
assert.match(en, /At least 1\.4×/);
assert.match(en, /Above pivot 2\.1%/);
assert.match(en, /Pocket Pivot demand confirmation/);
assert.match(en, /Insufficient history/);
assert.match(en, /Contraction depths did not decrease/);
assert.match(en, /Current volume 1\.25M/);
assert.match(en, /Supply pressure proxy/);
assert.match(en, /Demand confirmation proxy/);
assert.match(en, /Evidence coverage 92/);
assert.match(en, /Supply-demand state Two-way contest/);
assert.match(en, /Supply pressure proxy[^]*Threshold 50/);
assert.match(en, /Macro context/);
assert.match(en, /Decision permission/);

const notPrecomputed = structuredClone(forecast);
Object.assign(notPrecomputed.model_outputs.primary[0], {
  evidence_status: "not_precomputed",
  direction_accuracy: 0,
  always_up_direction_accuracy: 0,
  balanced_accuracy: 0,
  non_overlapping_sample_count: 0,
  non_overlapping_direction_accuracy: 0,
});
renderModelOutputs(container, {
  forecast: notPrecomputed,
  date: "2026-07-23",
  locale: "zh-CN",
  registry: externalRegistry,
});
const notPrecomputedText = textTree(container);
assert.match(notPrecomputedText, /尚未预计算/);
assert.doesNotMatch(notPrecomputedText, /方向准确率/);
assert.doesNotMatch(notPrecomputedText, /始终上涨基线/);

const oldReliabilityPayload = structuredClone(forecast);
delete oldReliabilityPayload.model_outputs.primary[0].direction_reliability;
delete oldReliabilityPayload.model_outputs.decision.primary_direction_reliability;
renderModelOutputs(container, {
  forecast: oldReliabilityPayload,
  date: "2026-07-23",
  locale: "zh-CN",
  registry: externalRegistry,
});
const oldReliabilityBadges = descendants(container)
  .filter((node) => node.className.includes("model-output-reliability-badge"));
assert.equal(oldReliabilityBadges.length, 0);
assert.doesNotMatch(textTree(container), /主预测可靠性/);

const legacyForecast = structuredClone(forecast);
delete legacyForecast.model_outputs.macro_context;
renderModelOutputs(container, {
  forecast: legacyForecast,
  date: "2026-07-23",
  locale: "zh-CN",
});
const legacyCards = descendants(container)
  .filter((node) => node.dataset.modelCard);
assert.equal(legacyCards.length, 10);
assert.match(textTree(container), /最终决策/);

const mismatchedForecast = structuredClone(forecast);
mismatchedForecast.model_outputs.registry_ref = "model_output_registry_v0";
renderModelOutputs(container, {
  forecast: mismatchedForecast,
  date: "2026-07-23",
  locale: "zh-CN",
  registry: externalRegistry,
});
const mismatchedCards = descendants(container)
  .filter((node) => node.dataset.modelCard);
assert.equal(mismatchedCards.length, 11);

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

console.log(JSON.stringify({
  zh, en, notPrecomputedText, loading, cardCount: cards.length,
}));
