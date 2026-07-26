const STORAGE_KEY = "quant-workstation.chart-marker-layers";

export const MARKER_LAYER_DEFINITIONS = Object.freeze([
  Object.freeze({ key: "strict_vcp", core: true }),
  Object.freeze({ key: "vcp_breakout", core: true }),
  Object.freeze({ key: "pocket_pivot", core: true }),
  Object.freeze({ key: "tight_platform", core: false }),
  Object.freeze({ key: "structure_reversal", core: false }),
  Object.freeze({ key: "early_reversal", core: false }),
  Object.freeze({ key: "prior_high_breakout", core: false }),
  Object.freeze({ key: "trendline_breakout", core: false }),
  Object.freeze({ key: "higher_low", core: false }),
]);

const KNOWN_LAYER_KEYS = new Set(MARKER_LAYER_DEFINITIONS.map(({ key }) => key));

export const MARKER_LAYER_PRESETS = Object.freeze({
  core: Object.freeze(
    MARKER_LAYER_DEFINITIONS.filter(({ core }) => core).map(({ key }) => key),
  ),
  all: Object.freeze(MARKER_LAYER_DEFINITIONS.map(({ key }) => key)),
  none: Object.freeze([]),
});

export function normalizeMarkerLayers(value) {
  if (!Array.isArray(value)) return [...MARKER_LAYER_PRESETS.core];
  const selected = new Set(value.filter((key) => KNOWN_LAYER_KEYS.has(key)));
  return MARKER_LAYER_DEFINITIONS
    .map(({ key }) => key)
    .filter((key) => selected.has(key));
}

export function readMarkerLayers(storage = globalThis.localStorage) {
  try {
    const stored = storage?.getItem(STORAGE_KEY);
    if (stored == null) return [...MARKER_LAYER_PRESETS.core];
    const parsed = JSON.parse(stored);
    return Array.isArray(parsed)
      ? normalizeMarkerLayers(parsed)
      : [...MARKER_LAYER_PRESETS.core];
  } catch (_error) {
    return [...MARKER_LAYER_PRESETS.core];
  }
}

export function persistMarkerLayers(layers, storage = globalThis.localStorage) {
  try {
    storage?.setItem(STORAGE_KEY, JSON.stringify(normalizeMarkerLayers(layers)));
  } catch (_error) {
    // Storage may be unavailable in private mode; in-memory selection still works.
  }
}
