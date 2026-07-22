const STORAGE_KEY = "quant-dashboard-locale";

export const SUPPORTED_LOCALES = Object.freeze(["zh-CN", "en"]);

const messages = {
  "zh-CN": {
    "document.title": "量化研究工作台",
    "skip.research": "跳到研究面板",
    "locale.label": "界面语言",
    "locale.zh": "中文",
    "locale.en": "EN",
    "brand.eyebrow": "本地市场研究",
    "brand.title": "量化工作台",
    "brand.note": "仅供描述性诊断 · 未验证预测能力",
    "header.latestDate": "最近共同日期",
    "header.coverage": "覆盖范围",
    "header.noData": "无数据",
    "header.coverageValue": "{current}/{total} 当前",
    "universe.eyebrow": "本地数据库",
    "universe.title": "股票池",
    "universe.search": "搜索股票代码",
    "universe.searchPlaceholder": "例如 AAPL",
    "universe.sort.label": "排序方式",
    "universe.sort.ticker": "股票代码",
    "universe.sort.freshness": "数据新鲜度",
    "universe.sort.momentum": "动量百分位",
    "universe.sort.shape": "形态状态",
    "universe.sort.volatility": "波动率",
    "universe.sort.ascending": "升序",
    "universe.sort.descending": "降序",
    "universe.sort.ascendingAria": "按升序排列",
    "universe.sort.descendingAria": "按降序排列",
    "universe.filters.legend": "筛选条件",
    "universe.filters.strictVcp": "严格 VCP",
    "universe.filters.tightPlatform": "紧密平台",
    "universe.filters.nearPivot": "接近枢轴点",
    "universe.filters.fresh": "最新数据",
    "universe.filters.inactive": "仅非活跃 / 陈旧",
    "universe.loading": "正在加载本地股票池…",
    "universe.navAria": "股票池",
    "universe.shown": "显示 {shown}/{total} 只股票",
    "universe.none": "本地没有可用股票代码",
    "universe.noMatch": "没有股票代码符合当前视图。",
    "universe.momentum": "动量 P{percentile}",
    "universe.momentumMissing": "动量 —",
    "universe.noDate": "无日期",
    "universe.shape.strictVcp": "严格 VCP",
    "universe.shape.tightPlatform": "紧密平台",
    "universe.shape.nearPivot": "接近枢轴点",
    "universe.shape.none": "无形态",
    "security.eyebrow": "所选证券",
    "security.selectTicker": "选择股票代码",
    "security.state.waiting": "等待中",
    "security.state.loading": "加载中",
    "security.state.current": "当前",
    "security.state.stale": "陈旧",
    "security.state.inactive": "非活跃",
    "security.state.unavailable": "不可用",
    "security.localHistory": "本地价格历史",
    "security.close": "收盘价",
    "security.dailyChange": "日涨跌幅",
    "security.observationDate": "观察日期",
    "security.choose": "请从股票池中选择股票代码。",
    "security.loading": "正在从本地数据库加载 {ticker}…",
    "security.loaded": "已加载截至 {date} 的观察数据。",
    "security.unknownDate": "未知日期",
    "security.unavailableUntilUniverse": "本地股票池加载完成前无法查看股票研究。",
    "security.warningAria": "数据质量警告",
    "warning.missing_benchmark": "缺少基准数据",
    "warning.inactive_ticker": "非活跃股票代码",
    "warning.stale_ticker": "股票数据陈旧",
    "warning.insufficient_indicator_history": "指标历史不足",
    "request.failed": "本地仪表板无法完成请求",
    "chart.eyebrow": "价格与成交量",
    "chart.title": "市场历史",
    "chart.rangeAria": "图表日期范围",
    "chart.range.3m": "3个月",
    "chart.range.6m": "6个月",
    "chart.range.1y": "1年",
    "chart.range.2y": "2年",
    "chart.range.all": "全部",
    "chart.priceAria": "K线价格图",
    "chart.volumeAria": "成交量图",
    "chart.selectHint": "选择股票代码以查看 OHLCV 和指标值。",
    "chart.locked": "已锁定 · 点击图表解锁",
    "factor.eyebrow": "标准化诊断",
    "factor.title": "因子概览",
    "factor.disclaimer": "展示分数用于组织观察结果，并非概率。",
    "factor.empty": "选择股票代码后加载因子诊断。",
    "factor.details": "因子明细表",
    "factor.caption": "原始因子观察值与方法说明",
    "factor.column.factor": "因子",
    "factor.column.formatted": "格式化值",
    "factor.column.raw": "原始值",
    "factor.column.percentile": "百分位 / 同期样本",
    "factor.column.score": "展示分数",
    "factor.column.date": "日期",
    "factor.column.description": "说明 / 版本",
    "factor.column.methodology": "方法说明",
    "factor.column.missing": "缺失原因",
    "structure.eyebrow": "描述性形态",
    "structure.title": "结构",
    "structure.empty": "选择股票代码后加载结构诊断。",
    "scenario.eyebrow": "历史条件区间",
    "scenario.title": "情景",
    "scenario.disclaimer": "历史情景描述区间，并非预测概率。",
    "scenario.chartAria": "历史情景图",
    "scenario.empty": "选择股票代码后加载情景方法说明。",
    "scenario.path.pessimistic": "历史悲观情景",
    "scenario.path.median": "历史中位情景",
    "scenario.path.optimistic": "历史乐观情景",
    "scenario.seriesTitle": "{sessions} 个交易日 · {label}",
    "scenario.sessions": "{sessions} 个交易日",
    "scenario.samples.historical": "{count} 个历史样本",
    "scenario.samples.nonOverlapping": "{count} 个非重叠样本",
    "scenario.methodologyUnavailable": "方法说明不可用",
    "scenario.observationDate": "观察日期：{date}",
    "scenario.libraryUnavailable": "本地图表库不可用。",
    "scenario.insufficientHorizons": "没有历史情景周期具备足够样本。",
    "scenario.sessionTick": "第{session}日",
    "scenario.sessionFull": "第 {session} 个交易日",
    "scenario.missing.insufficient_samples": "样本不足",
    "update.button.start": "更新市场数据",
    "update.button.resume": "继续价格更新",
    "update.state.idle": "仅价格更新状态：空闲",
    "update.state.running": "仅价格更新进行中：已检查 {completed}/{total} · 已更新 {updated}{ticker}",
    "update.state.runningTicker": " · 正在检查 {ticker}",
    "update.state.completed": "仅价格更新完成：已更新 {updated}/{total}。",
    "update.state.partial": "仅价格更新以部分结果停止：已检查 {completed}/{total} · 已更新 {updated}。",
    "update.state.rateLimited": "检查 {completed}/{total} 后触发速率限制；已更新 {updated}。继续操作会保留剩余任务。",
    "update.state.failed": "仅价格更新失败：已检查 {completed}/{total}；已更新 {updated}。",
    "update.statusUnavailable": "更新状态暂时不可用。",
    "update.statusRetrying": "更新状态暂时不可用；任务仍在运行并重试。",
    "update.startFailed": "无法启动价格更新",
    "footer.disclaimer": "仅供研究与教育。本工作台不提供投资建议。",
    "footer.attribution": "图表由 TradingView 提供",
  },
  en: {
    "debug.englishFallback": "English fallback",
    "document.title": "Quant Research Workstation",
    "skip.research": "Skip to research",
    "locale.label": "Interface language",
    "locale.zh": "中文",
    "locale.en": "EN",
    "brand.eyebrow": "Local market research",
    "brand.title": "Quant Workstation",
    "brand.note": "Descriptive diagnostics only · Not validated for prediction",
    "header.latestDate": "Latest common date",
    "header.coverage": "Coverage",
    "header.noData": "No data",
    "header.coverageValue": "{current}/{total} current",
    "universe.eyebrow": "Local database",
    "universe.title": "Stock pool",
    "universe.search": "Search ticker",
    "universe.searchPlaceholder": "e.g. AAPL",
    "universe.sort.label": "Sort by",
    "universe.sort.ticker": "Ticker",
    "universe.sort.freshness": "Data freshness",
    "universe.sort.momentum": "Momentum percentile",
    "universe.sort.shape": "Shape state",
    "universe.sort.volatility": "Volatility",
    "universe.sort.ascending": "Ascending",
    "universe.sort.descending": "Descending",
    "universe.sort.ascendingAria": "Sort ascending",
    "universe.sort.descendingAria": "Sort descending",
    "universe.filters.legend": "Filters",
    "universe.filters.strictVcp": "Strict VCP",
    "universe.filters.tightPlatform": "Tight platform",
    "universe.filters.nearPivot": "Near pivot",
    "universe.filters.fresh": "Fresh data",
    "universe.filters.inactive": "Inactive / stale only",
    "universe.loading": "Loading local universe…",
    "universe.navAria": "Stock pool",
    "universe.shown": "Showing {shown}/{total} stocks",
    "universe.none": "No local tickers available",
    "universe.noMatch": "No tickers match the current view.",
    "universe.momentum": "Momentum P{percentile}",
    "universe.momentumMissing": "Momentum —",
    "universe.noDate": "No date",
    "universe.shape.strictVcp": "Strict VCP",
    "universe.shape.tightPlatform": "Tight platform",
    "universe.shape.nearPivot": "Near pivot",
    "universe.shape.none": "No shape",
    "security.eyebrow": "Selected security",
    "security.selectTicker": "Select a ticker",
    "security.state.waiting": "Waiting",
    "security.state.loading": "Loading",
    "security.state.current": "Current",
    "security.state.stale": "Stale",
    "security.state.inactive": "Inactive",
    "security.state.unavailable": "Unavailable",
    "security.localHistory": "Local price history",
    "security.close": "Close",
    "security.dailyChange": "Daily change",
    "security.observationDate": "Observation date",
    "security.choose": "Choose a ticker from the stock pool.",
    "security.loading": "Loading {ticker} from the local database…",
    "security.loaded": "Loaded observations through {date}.",
    "security.unknownDate": "an unknown date",
    "security.unavailableUntilUniverse": "Stock research is unavailable until the local universe loads.",
    "security.warningAria": "Data quality warnings",
    "warning.missing_benchmark": "missing benchmark",
    "warning.inactive_ticker": "inactive ticker",
    "warning.stale_ticker": "stale ticker",
    "warning.insufficient_indicator_history": "insufficient indicator history",
    "request.failed": "The local dashboard could not complete the request",
    "chart.eyebrow": "Price and volume",
    "chart.title": "Market history",
    "chart.rangeAria": "Chart date range",
    "chart.range.3m": "3M",
    "chart.range.6m": "6M",
    "chart.range.1y": "1Y",
    "chart.range.2y": "2Y",
    "chart.range.all": "All",
    "chart.priceAria": "Candlestick price chart",
    "chart.volumeAria": "Volume chart",
    "chart.selectHint": "Select a ticker to inspect OHLCV and indicator values.",
    "chart.locked": "Locked · click a chart to unlock",
    "factor.eyebrow": "Normalized diagnostics",
    "factor.title": "Factor overview",
    "factor.disclaimer": "Display scores organize observations; they are not probabilities.",
    "factor.empty": "Factor diagnostics load with a selected ticker.",
    "factor.details": "Factor detail table",
    "factor.caption": "Raw factor observations and methodology",
    "factor.column.factor": "Factor",
    "factor.column.formatted": "Formatted value",
    "factor.column.raw": "Raw value",
    "factor.column.percentile": "Percentile / peers",
    "factor.column.score": "Display score",
    "factor.column.date": "Date",
    "factor.column.description": "Description / version",
    "factor.column.methodology": "Methodology",
    "factor.column.missing": "Missing reason",
    "structure.eyebrow": "Descriptive setup",
    "structure.title": "Structure",
    "structure.empty": "Structure diagnostics load with a selected ticker.",
    "scenario.eyebrow": "Historical conditional ranges",
    "scenario.title": "Scenarios",
    "scenario.disclaimer": "Historical scenarios describe ranges; they are not predicted probabilities.",
    "scenario.chartAria": "Historical scenario chart",
    "scenario.empty": "Scenario methodology loads with a selected ticker.",
    "scenario.path.pessimistic": "Pessimistic historical scenario",
    "scenario.path.median": "Median historical scenario",
    "scenario.path.optimistic": "Optimistic historical scenario",
    "scenario.seriesTitle": "{sessions} sessions · {label}",
    "scenario.sessions": "{sessions} sessions",
    "scenario.samples.historical": "{count} historical sample{suffix}",
    "scenario.samples.nonOverlapping": "{count} non-overlapping sample{suffix}",
    "scenario.methodologyUnavailable": "Methodology unavailable",
    "scenario.observationDate": "Observation date: {date}",
    "scenario.libraryUnavailable": "The local chart library is unavailable.",
    "scenario.insufficientHorizons": "No historical scenario horizon has enough samples.",
    "scenario.sessionTick": "S{session}",
    "scenario.sessionFull": "Session {session}",
    "scenario.missing.insufficient_samples": "insufficient samples",
    "update.button.start": "Update market data",
    "update.button.resume": "Resume price update",
    "update.state.idle": "Price-only update status: idle",
    "update.state.running": "Price-only update running: {completed}/{total} checked · {updated} updated{ticker}",
    "update.state.runningTicker": " · checking {ticker}",
    "update.state.completed": "Price-only update finished: {updated}/{total} updated.",
    "update.state.partial": "Price-only update stopped with partial results: {completed}/{total} checked · {updated} updated.",
    "update.state.rateLimited": "Rate limited after {completed}/{total} checked; {updated} updated. Resume preserves remaining work.",
    "update.state.failed": "Price-only update failed after {completed}/{total} checked; {updated} updated.",
    "update.statusUnavailable": "Update status is temporarily unavailable.",
    "update.statusRetrying": "Update status is temporarily unavailable; still running and retrying.",
    "update.startFailed": "Unable to start price update",
    "footer.disclaimer": "Research and education only. This workstation does not provide investment advice.",
    "footer.attribution": "Charts by TradingView",
  },
};

function normalizeLocale(locale) {
  return SUPPORTED_LOCALES.includes(locale) ? locale : "zh-CN";
}

function readStoredLocale() {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY);
  } catch (_error) {
    return null;
  }
}

let currentLocale = normalizeLocale(readStoredLocale());
const listeners = new Set();

export function getLocale() {
  return currentLocale;
}

export function setLocale(locale) {
  currentLocale = normalizeLocale(locale);
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, currentLocale);
  } catch (_error) {
    // Browser privacy settings can disable storage; locale still works in memory.
  }
  listeners.forEach((listener) => listener(currentLocale));
  return currentLocale;
}

export function subscribeLocale(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function t(key, params = {}, locale = currentLocale) {
  const selectedLocale = normalizeLocale(locale);
  const message = messages[selectedLocale]?.[key] ?? messages.en?.[key] ?? key;
  return message.replace(/\{(\w+)\}/g, (match, name) => (
    Object.hasOwn(params, name) ? String(params[name]) : match
  ));
}

export function applyDocumentLocale(root = globalThis.document, locale = currentLocale) {
  const selectedLocale = normalizeLocale(locale);
  if (!root) return selectedLocale;
  const documentElement = root.documentElement || root.ownerDocument?.documentElement;
  if (documentElement) documentElement.lang = selectedLocale;

  root.querySelectorAll?.("[data-i18n]").forEach((element) => {
    element.textContent = t(element.dataset.i18n, {}, selectedLocale);
  });
  root.querySelectorAll?.("[data-i18n-placeholder]").forEach((element) => {
    element.setAttribute("placeholder", t(element.dataset.i18nPlaceholder, {}, selectedLocale));
  });
  root.querySelectorAll?.("[data-i18n-aria-label]").forEach((element) => {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel, {}, selectedLocale));
  });
  root.querySelectorAll?.("[data-locale]").forEach((element) => {
    element.setAttribute("aria-pressed", String(element.dataset.locale === selectedLocale));
  });
  return selectedLocale;
}

function validDateParts(year, month, day) {
  if (![year, month, day].every(Number.isInteger)) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day
    ? { year, month, day }
    : null;
}

function dateParts(value) {
  if (typeof value === "string") {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(value);
    return match ? validDateParts(...match.slice(1).map(Number)) : null;
  }
  if (value && typeof value === "object" && !(value instanceof Date)) {
    return validDateParts(value.year, value.month, value.day);
  }
  if (value instanceof Date && !Number.isNaN(value.valueOf())) {
    return validDateParts(
      value.getUTCFullYear(), value.getUTCMonth() + 1, value.getUTCDate(),
    );
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    const date = new Date(value * 1000);
    return validDateParts(
      date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(),
    );
  }
  return null;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

export function formatChartTickDate(value) {
  const part = dateParts(value);
  return part ? `${pad(part.month)}-${pad(part.day)}` : "—";
}

export function formatFullDate(value) {
  const part = dateParts(value);
  return part ? `${part.year}-${pad(part.month)}-${pad(part.day)}` : "—";
}
