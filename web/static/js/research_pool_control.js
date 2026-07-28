import { getLocale, t, translateError } from "./i18n.js";


export function createResearchPoolControl({
  button,
  status,
  apiClient,
  onChanged = async () => {},
  locale = getLocale(),
} = {}) {
  let selectedTicker = null;
  let membership = {};
  let currentLocale = locale;
  let busy = false;

  function render() {
    if (!button) return;
    const included = Boolean(membership.research);
    button.hidden = !selectedTicker;
    button.disabled = busy || !selectedTicker;
    button.dataset.included = String(included);
    button.textContent = t(
      included ? "researchPool.action.exit" : "researchPool.action.join",
      {},
      currentLocale,
    );
  }

  async function toggle() {
    if (!selectedTicker || busy) return;
    const ticker = selectedTicker;
    const included = !Boolean(membership.research);
    busy = true;
    render();
    if (status) {
      status.textContent = t(
        "researchPool.action.saving",
        { ticker },
        currentLocale,
      );
      status.dataset.tone = "loading";
    }
    try {
      const payload = await apiClient.setResearchPoolMembership(
        ticker,
        included,
      );
      membership = { ...membership, research: Boolean(payload.research) };
      await onChanged(payload);
      if (status) {
        status.textContent = t(
          payload.research
            ? "researchPool.action.joined"
            : "researchPool.action.exited",
          { ticker },
          currentLocale,
        );
        status.dataset.tone = "success";
      }
    } catch (error) {
      if (status) {
        status.textContent = translateError(
          error,
          "researchPool.action.failed",
          currentLocale,
        );
        status.dataset.tone = "error";
      }
    } finally {
      busy = false;
      render();
    }
  }

  button?.addEventListener("click", toggle);
  render();

  return Object.freeze({
    setSelection(ticker, nextMembership = {}) {
      const nextTicker = ticker ? String(ticker).toUpperCase() : null;
      if (status && nextTicker !== selectedTicker) {
        status.textContent = "";
        status.removeAttribute?.("data-tone");
        if (status.dataset) delete status.dataset.tone;
      }
      selectedTicker = nextTicker;
      membership = { ...nextMembership };
      render();
    },
    setLocale(nextLocale) {
      currentLocale = nextLocale || getLocale();
      render();
    },
  });
}
