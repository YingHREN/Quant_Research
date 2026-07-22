# Task 2 Report: Complete Chinese/English Interface Switching

## Result

Implemented persistent, reload-free Simplified Chinese/English switching across
the existing dashboard while preserving the Task 1 localization API. The
header now exposes two accessible locale buttons; static copy, ARIA labels,
range controls, universe rows, stock status/warnings, update progress, and
historical scenarios rerender from stable translation keys.

## Files changed

- `web/templates/index.html`: adds the two-button locale selector and
  localization keys for all visible static labels, placeholders, and ARIA
  labels.
- `web/static/js/i18n.js`: adds complete `zh-CN`/English catalogs and exports
  `applyDocumentLocale(root, locale)` without changing Task 1 exports.
- `web/static/js/app.js`: binds locale controls, subscribes to locale changes,
  and rerenders stateful dashboard content without reloading or changing
  payload identifiers.
- `web/static/js/universe.js`: localizes ticker states, shape labels, momentum,
  dates, and empty results while continuing to compare raw shape identifiers.
- `web/static/js/update.js`: localizes every update state/button/error fallback
  and rerenders the last state when locale changes; unknown server messages are
  retained verbatim.
- `web/static/js/scenarios.js`: localizes scenario paths, horizons, samples,
  missing states, chart fallbacks, and session labels while preserving raw
  horizon/path identifiers and server-provided methodology.
- `web/static/css/dashboard.css`: styles pressed/focus-visible locale controls
  and keeps both controls usable at 390px.
- `tests/test_web_assets.py`: adds the bilingual runtime/template contract,
  mobile/focus assertions, direct locale subscriber/unsubscribe coverage, and
  direct English-fallback coverage.

## TDD evidence

### RED

After adding `test_bilingual_dashboard`, the requested focused test was run:

```sh
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_bilingual_dashboard -v
```

It failed with `AssertionError: 0 != 2` because the template contained no
`[data-locale]` buttons.

### GREEN

The focused bilingual test and direct Task 1 regression test both passed after
implementation. The full asset suite then passed:

```sh
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets -v
```

Output: `Ran 23 tests ... OK`.

Fresh full-suite verification:

```sh
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task2-pycache /Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest discover -v
```

Output: `Ran 118 tests ... OK`.

JavaScript syntax checks for all five changed modules and `git diff --check`
also passed. A catalog audit resolved all 77 localization keys referenced by
the template in both locales with no missing entries.

## Self-review

- `SUPPORTED_LOCALES`, persistence, interpolation, deterministic date helpers,
  and existing Task 1 signatures remain intact.
- Locale changes update `<html lang>`, `aria-pressed`, range text, chart ARIA
  labels, and active update status in place.
- Every visible static template label has a stable localization key; technical
  identifiers such as VCP, OHLCV, ticker symbols, and raw payload state values
  remain stable.
- Universe/update/scenario comparisons operate on original server identifiers;
  translation happens only when rendering.
- Known warnings and missing reasons are translated. Unknown server messages
  are displayed in their original form rather than hidden or guessed.
- Locale subscriptions are unsubscribed by update-controller destruction and
  page teardown.
- The locale buttons inherit the existing visible focus ring, expose pressed
  state, and expand evenly in the 390px layout.
- No spec or plan files were edited.

## Concerns

- The isolated worktree has no `./venv`; verification used the repository
  virtualenv at `/Users/renyinghao.1/Project/stock_screener/venv`.
- The in-app browser runtime reported no available browser, so live visual QA
  could not run. Responsive/focus behavior is covered by asset assertions and
  the local Flask server started successfully.

---

## Review-fix pass (2026-07-22)

### Result

Resolved every Task 2 review finding and the follow-up code-review findings.
The actual dashboard locale-button path now refreshes chart, factor, structure,
scenario, application-error, and update-error state without a reload.

### Changes

- `web/static/js/charts.js`: localizes OHLCV detail labels, cross states,
  empty/hover/lock copy, volume-series titles, pivot labels, and shape markers.
  The controller now exposes `setLocale(locale)` and preserves the displayed
  observation and click-lock state while repainting localized chart metadata.
- `web/static/js/factors.js`: localizes percentile/peer copy, known first-party
  factor and group metadata, missing reasons, empty states, meter ARIA labels,
  and known structure keys. Unknown factor/group/structure metadata continues
  to use the payload or humanized safe fallback. No popover or backend `i18n`
  contract from Task 4 was introduced.
- `web/static/js/i18n.js`: adds stable chart/factor/structure/scenario/error
  catalog keys and `translateError`. Known API codes/messages render safe
  localized copy; unknown messages remain visible as the compatibility
  fallback. Error-map lookup uses own-property checks so prototype-like future
  codes such as `constructor` cannot crash translation.
- `web/static/js/app.js`: passes locale into stateful renderers, explicitly
  refreshes the linked-chart controller, retains raw structured errors for
  retranslation, refreshes header coverage, and preserves universe/research
  error text, tone, and unavailable security state across locale changes.
- `web/static/js/update.js`: retains structured start errors and rerenders them
  when locale changes instead of exposing known English text and dropping
  localization state.
- `web/static/js/scenarios.js`: localizes the known
  `historical_distribution` provider and horizon methodology through stable
  provider keys; unknown providers retain their supplied methodology.
- `tests/dashboard_runtime.mjs`: provides a dependency-free DOM/chart/fetch
  runtime harness that initializes the real `app.js`, clicks the actual locale
  control handler, and observes dynamic renderers and failure states.
- `tests/test_web_assets.py`: adds runtime integration coverage plus focused
  stable-key, fallback, error-collision, empty-state, and ARIA regressions.

### TDD evidence

The first RED run used the new actual-dashboard and focused renderer tests:

```sh
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_actual_dashboard_locale_switch_refreshes_dynamic_renderers \
  tests.test_web_assets.WebAssetTest.test_actual_dashboard_locale_switch_preserves_safe_error_states \
  tests.test_web_assets.WebAssetTest.test_known_update_errors_are_localized_and_unknown_errors_remain_safe_fallbacks \
  tests.test_web_assets.WebAssetTest.test_factor_localization_uses_stable_keys_with_safe_unknown_fallbacks \
  tests.test_web_assets.WebAssetTest.test_scenario_methodology_localizes_known_provider_and_preserves_unknown -v
```

Output: `Ran 5 tests ... FAILED (errors=5)`. The failures were the intended
behavior gaps: English factor/scenario/chart output, raw known server errors,
and dropped universe failure state.

After the first implementation pass, the same five tests reported
`Ran 5 tests ... OK`. The initial focused asset-suite run then exposed four
outdated English-default assertions; after making locale explicit and updating
the known-provider expectations, `tests.test_web_assets` reported
`Ran 28 tests ... OK`.

The independent code-review pass found three further edge cases. Regressions
were added first for a prototype-collision error code, structure localization,
and the initial unavailable security badge. The RED run reported
`Ran 4 tests ... FAILED (errors=4)`; after the fixes, it reported
`Ran 4 tests ... OK`.

### Final verification

```sh
node --check web/static/js/i18n.js
node --check web/static/js/app.js
node --check web/static/js/charts.js
node --check web/static/js/factors.js
node --check web/static/js/scenarios.js
node --check web/static/js/update.js
node --check tests/dashboard_runtime.mjs
```

Output: all seven syntax checks exited `0` with no output.

```sh
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task2-fix-pycache \
  /Users/renyinghao.1/Project/stock_screener/venv/bin/python \
  -m unittest tests.test_web_assets -v
```

Output: `Ran 28 tests in 0.979s` and `OK`.

```sh
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task2-fix-pycache \
  /Users/renyinghao.1/Project/stock_screener/venv/bin/python \
  -W error -m unittest discover -v
```

Output: `Ran 123 tests in 1.725s` and `OK`.

```sh
git diff --check
```

Output: exit `0`, no whitespace errors.

### Review and concerns

- Independent review reported no Critical findings. Its three Important
  findings were fixed with RED/GREEN regressions before final verification.
- Unknown provider/factor/error text is intentionally preserved only as a
  forward-compatible fallback; known first-party identifiers always use safe
  catalog copy.
- The runtime harness exercises the real application/controller path with a
  small in-process DOM and Lightweight Charts test double. Live visual browser
  QA remains unavailable in this environment, matching the original report.
