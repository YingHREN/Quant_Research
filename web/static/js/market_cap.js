import { t } from "./i18n.js";

function compact(value, digits) {
  return Number(value.toFixed(digits)).toString();
}

export function formatMarketCap(value) {
  if (!Number.isFinite(value) || value <= 0) return null;
  if (value >= 1e12) return `$${compact(value / 1e12, 2)}T`;
  if (value >= 1e9) return `$${compact(value / 1e9, 1)}B`;
  if (value >= 1e6) return `$${compact(value / 1e6, 0)}M`;
  if (value >= 1e3) return `$${compact(value / 1e3, 0)}K`;
  return `$${Math.round(value)}`;
}

export function marketCapLabel(row = {}, locale) {
  const amount = formatMarketCap(row.market_cap);
  const tier = row.market_cap_tier || "unavailable";
  if (!amount || tier === "unavailable") {
    return t("marketCap.unavailable", {}, locale);
  }
  return t(
    "marketCap.label",
    {
      tier: t(`marketCap.tier.${tier}`, {}, locale),
      amount,
    },
    locale,
  );
}

export function marketCapAccessibleLabel(row = {}, locale) {
  const label = marketCapLabel(row, locale);
  return row.market_cap_asof
    ? t(
      "marketCap.asof",
      { label, date: row.market_cap_asof },
      locale,
    )
    : label;
}
