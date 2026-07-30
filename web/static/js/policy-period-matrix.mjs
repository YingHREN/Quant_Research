const METRIC_KEYS = Object.freeze([
  "total_return",
  "annualized_return",
  "relative_spy_return",
  "max_drawdown",
  "positive_month_ratio",
]);

function node(document, tag, className = "") {
  const result = document.createElement(tag);
  if (className) result.className = className;
  return result;
}

function setText(target, value) {
  target.textContent = value == null ? "—" : String(value);
  return target;
}

function localizedPeriod(period, locale) {
  return locale === "zh-CN"
    ? period.label_zh || period.label_en
    : period.label_en || period.label_zh;
}

function localizedInterpretation(period, locale) {
  return locale === "zh-CN"
    ? period.interpretation_zh || period.interpretation_en
    : period.interpretation_en || period.interpretation_zh;
}

function metricText(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function tone(value) {
  if (!Number.isFinite(Number(value)) || Number(value) === 0) {
    return "neutral";
  }
  return Number(value) > 0 ? "positive" : "negative";
}

function periodStatus(period, translate) {
  return translate(
    period.is_complete
      ? "market.policyMatrix.period.complete"
      : "market.policyMatrix.period.incomplete",
  );
}

function renderPeriodDetail({
  document,
  detail,
  period,
  payload,
  locale,
  translate,
}) {
  if (!period) {
    detail.replaceChildren();
    return;
  }
  const heading = node(document, "div", "policy-period-detail-heading");
  heading.append(
    setText(node(document, "h4"), localizedPeriod(period, locale)),
    setText(
      node(document, "span"),
      periodStatus(period, translate),
    ),
  );
  const dates = setText(
    node(document, "p", "policy-period-detail-meta"),
    translate("market.policyMatrix.periodDates", {
      start: period.start_date || "—",
      end: period.end_date
        || translate("market.policyMatrix.period.incomplete"),
    }),
  );
  const interpretation = setText(
    node(document, "p", "policy-period-interpretation"),
    localizedInterpretation(period, locale)
      || translate("market.policyMatrix.descriptionOnly"),
  );
  const sourceHeading = setText(
    node(document, "h5", "policy-period-source-title"),
    translate("market.policyMatrix.sourceTitle"),
  );
  const sources = node(document, "ul", "policy-period-sources");
  if (period.events?.length) {
    for (const event of period.events) {
      const item = node(document, "li");
      const link = node(document, "a");
      link.href = event.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      setText(
        link,
        `${event.effective_date || "—"} · ${event.source_title || event.event_id}`,
      );
      item.append(link);
      sources.append(item);
    }
  } else {
    sources.append(
      setText(
        node(document, "li"),
        translate("market.policyMatrix.noEvents"),
      ),
    );
  }
  const authority = setText(
    node(document, "p", "policy-period-detail-authority"),
    translate("market.policyMatrix.authority", {
      lifecycle: payload.lifecycle || "research",
      permission: payload.decision_permission || "advisory",
      authority: payload.online_authority || "none",
    }),
  );
  detail.replaceChildren(
    heading,
    dates,
    interpretation,
    sourceHeading,
    sources,
    authority,
  );
}

export function renderPolicyPeriodMatrixView({
  document,
  root,
  detail,
  payload = {},
  metric = "total_return",
  locale = "zh-CN",
  translate,
  selectedPeriodId = null,
  onSelectPeriod = null,
}) {
  const activeMetric = METRIC_KEYS.includes(metric)
    ? metric
    : "total_return";
  if (payload.unavailable_reason || !payload.periods?.length) {
    root.replaceChildren(
      setText(
        node(document, "p", "market-empty"),
        translate(
          `market.unavailable.${payload.unavailable_reason
            || "policy_catalog_unavailable"}`,
        ),
      ),
    );
    detail.replaceChildren();
    return;
  }

  const periods = payload.periods;
  const selected = periods.find(
    (period) => period.period_id === selectedPeriodId,
  ) || periods[0];
  const byCell = new Map(
    (payload.rows || []).map(
      (row) => [`${row.ticker}:${row.period_id}`, row],
    ),
  );
  const tickers = [...new Set(
    (payload.rows || []).map((row) => row.ticker),
  )];
  const scroll = node(document, "div", "policy-period-matrix-scroll");
  const table = node(document, "table", "policy-period-table");
  const head = node(document, "thead");
  const headingRow = node(document, "tr");
  headingRow.append(
    setText(
      node(document, "th"),
      translate("market.policyMatrix.symbol"),
    ),
  );
  for (const period of periods) {
    const cell = node(document, "th");
    const button = node(document, "button", "policy-period-select");
    button.type = "button";
    button.dataset.periodId = period.period_id;
    button.setAttribute(
      "aria-pressed",
      String(period.period_id === selected.period_id),
    );
    setText(button, localizedPeriod(period, locale));
    if (
      typeof onSelectPeriod === "function"
      && typeof button.addEventListener === "function"
    ) {
      button.addEventListener(
        "click",
        () => onSelectPeriod(period.period_id),
      );
    }
    cell.append(button);
    headingRow.append(cell);
  }
  head.append(headingRow);
  table.append(head);

  const body = node(document, "tbody");
  for (const ticker of tickers) {
    const rowNode = node(document, "tr");
    rowNode.append(setText(node(document, "th"), ticker));
    for (const period of periods) {
      const cell = node(document, "td");
      const row = byCell.get(`${ticker}:${period.period_id}`);
      if (row?.status === "complete") {
        cell.dataset.tone = tone(row[activeMetric]);
        setText(cell, metricText(row[activeMetric]));
      } else {
        cell.dataset.status = row?.status || "missing_history";
        setText(
          cell,
          translate(
            `market.policyMatrix.status.${row?.status
              || "missing_history"}`,
          ),
        );
      }
      rowNode.append(cell);
    }
    body.append(rowNode);
  }
  table.append(body);
  scroll.append(table);
  root.replaceChildren(scroll);
  renderPeriodDetail({
    document,
    detail,
    period: selected,
    payload,
    locale,
    translate,
  });
}
