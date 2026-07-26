import assert from "node:assert/strict";

const [moduleUri] = process.argv.slice(2);
const {
  MARKER_LAYER_DEFINITIONS,
  MARKER_LAYER_PRESETS,
  normalizeMarkerLayers,
  persistMarkerLayers,
  readMarkerLayers,
} = await import(moduleUri);

assert.equal(MARKER_LAYER_DEFINITIONS.length, 10);
assert.ok(MARKER_LAYER_PRESETS.all.includes("top_risk"));
assert.ok(!MARKER_LAYER_PRESETS.core.includes("top_risk"));
assert.deepEqual(
  normalizeMarkerLayers(undefined),
  ["strict_vcp", "vcp_breakout", "pocket_pivot"],
);
assert.deepEqual(
  normalizeMarkerLayers(["pocket_pivot", "unknown", "pocket_pivot"]),
  ["pocket_pivot"],
);
assert.deepEqual(
  normalizeMarkerLayers(MARKER_LAYER_PRESETS.all),
  MARKER_LAYER_DEFINITIONS.map(({ key }) => key),
);
assert.deepEqual(normalizeMarkerLayers([]), []);

const brokenStorage = {
  getItem() {
    return "{not-json";
  },
};
assert.deepEqual(readMarkerLayers(brokenStorage), MARKER_LAYER_PRESETS.core);

const unavailableStorage = {
  getItem() {
    throw new Error("storage disabled");
  },
};
assert.deepEqual(readMarkerLayers(unavailableStorage), MARKER_LAYER_PRESETS.core);

const writableStorage = {
  value: null,
  setItem(_key, value) {
    this.value = value;
  },
};
persistMarkerLayers(["pocket_pivot", "unknown"], writableStorage);
assert.deepEqual(JSON.parse(writableStorage.value), ["pocket_pivot"]);

const rejectingStorage = {
  setItem() {
    throw new Error("quota exceeded");
  },
};
assert.doesNotThrow(() => persistMarkerLayers(["pocket_pivot"], rejectingStorage));

process.stdout.write(JSON.stringify({
  core: MARKER_LAYER_PRESETS.core,
  all: MARKER_LAYER_PRESETS.all,
  persisted: JSON.parse(writableStorage.value),
}));
