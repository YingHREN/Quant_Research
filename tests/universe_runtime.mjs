import assert from "node:assert/strict";

const [providedAppUri] = process.argv.slice(2);
const appUri = providedAppUri
  || new URL("../web/static/js/app.js", import.meta.url).href;

class Element {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.className = "";
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

const { renderGroupAssignmentCard } = await import(appUri);
globalThis.document = {
  createElement(tagName) {
    return new Element(tagName);
  },
};

const sndk = {
  state: "assigned",
  ticker: "SNDK",
  rule_version: "security_group_overrides_v1",
  sector_key: "technology",
  sector_benchmark: "XLK",
  theme_keys: ["semiconductor"],
  theme_benchmarks: { semiconductor: ["SOXX", "SMH"] },
  primary_model_group: "semiconductor",
  classification_state: "needs_review",
  source: "manual_override",
  confidence: 0.92,
  override_reason: "flash memory and storage semiconductor exposure",
};

const zhCard = renderGroupAssignmentCard(sndk, "zh-CN");
const zh = textTree(zhCard);
assert.match(zh, /科技 \/ XLK/);
assert.match(zh, /半导体 \/ SOXX\+SMH/);
assert.match(zh, /主要模型分组.*半导体/);
assert.match(zh, /来源.*manual_override/);
assert.match(zh, /置信度 92%/);
assert.match(zh, /待复核/);
assert.ok(
  descendants(zhCard).some(
    (node) => /宽泛行业/.test(node.attributes.title || ""),
  ),
);

const enCard = renderGroupAssignmentCard(sndk, "en");
const en = textTree(enCard);
assert.match(en, /Technology \/ XLK/);
assert.match(en, /Semiconductor \/ SOXX\+SMH/);
assert.match(en, /Primary model group.*Semiconductor/);
assert.match(en, /Needs review/);
assert.ok(
  descendants(enCard).some(
    (node) => /Broad sector/.test(node.attributes.title || ""),
  ),
);

const nbis = renderGroupAssignmentCard(
  {
    ...sndk,
    ticker: "NBIS",
    theme_keys: [],
    theme_benchmarks: {},
    primary_model_group: "technology",
    classification_state: "classified",
    source: "sec_broad",
  },
  "en",
);
const nbisText = textTree(nbis);
assert.match(nbisText, /Technology \/ XLK/);
assert.doesNotMatch(nbisText, /Semiconductor|SOXX|SMH/);

const missing = renderGroupAssignmentCard(
  {
    state: "missing",
    reason: "assignment_repository_unavailable",
  },
  "zh-CN",
);
assert.match(textTree(missing), /分组不可用/);
assert.equal(missing.dataset.state, "missing");
