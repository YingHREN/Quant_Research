// Pure marker-lane compaction for Lightweight Charts.

function markerTime(value) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (
    value
    && Number.isInteger(value.year)
    && Number.isInteger(value.month)
    && Number.isInteger(value.day)
  ) {
    return `${value.year}-${String(value.month).padStart(2, "0")}-${String(value.day).padStart(2, "0")}`;
  }
  return "";
}

function publicMarker(marker, text) {
  const {
    priority: _priority,
    layoutGroup: _layoutGroup,
    text: _text,
    ...style
  } = marker;
  return { ...style, text };
}

export function layoutChartMarkers(markers = []) {
  const groups = new Map();
  (Array.isArray(markers) ? markers : []).forEach((marker, index) => {
    if (!marker || !markerTime(marker.time) || !marker.position) return;
    const layoutGroup = marker.layoutGroup === "forecast"
      ? `forecast:${index}`
      : "signals";
    const key = `${markerTime(marker.time)}|${marker.position}|${layoutGroup}`;
    const existing = groups.get(key);
    const text = marker.text == null || marker.text === ""
      ? []
      : [String(marker.text)];
    if (!existing) {
      groups.set(key, {
        marker,
        texts: text,
        priority: Number.isFinite(marker.priority) ? marker.priority : 0,
        index,
      });
      return;
    }
    for (const value of text) {
      if (!existing.texts.includes(value)) existing.texts.push(value);
    }
    const priority = Number.isFinite(marker.priority) ? marker.priority : 0;
    if (priority > existing.priority) {
      existing.marker = marker;
      existing.priority = priority;
    }
  });
  const laneOrder = { aboveBar: 0, inBar: 1, belowBar: 2 };
  return [...groups.values()]
    .sort((left, right) => (
      markerTime(left.marker.time).localeCompare(markerTime(right.marker.time))
      || (laneOrder[left.marker.position] ?? 9)
        - (laneOrder[right.marker.position] ?? 9)
      || left.index - right.index
    ))
    .map(({ marker, texts }) => publicMarker(marker, texts.join(" · ")));
}
