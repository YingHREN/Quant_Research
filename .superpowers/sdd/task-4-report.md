# Task 4 Report: Localized Factor Explanations

## Result

Built-in factors and groups now expose immutable, JSON-safe `zh-CN` metadata
for label, description, methodology, observation window, and favorable
direction. Existing English compatibility fields and the response shape for
third-party factors without localization remain unchanged.

Every rendered factor label has a real information button. All buttons share
one body-level popover with localized explanation content, current value, ISO
data date, version, and missing reason. The popover uses `textContent`, updates
`aria-expanded` / `aria-controls`, supports pointer, focus, click, Enter, Space,
Escape, and outside-click behavior, stays within the viewport, and closes
cleanly before factor rerenders.

Payload-provided translations take precedence, followed by the existing
stable-key catalog and then safe server-text/humanized fallbacks. This keeps
Task 2 localization behavior intact for older payloads and unknown extensions.

## TDD evidence

1. Backend metadata tests failed because built-ins and serialized results had
   no `i18n` metadata.
2. The backend suite passed after adding immutable nested mappings, optional
   serialization, built-in translations, and English observation windows.
3. The interaction test failed because factor information buttons and the
   reusable popover did not exist.
4. Pointer/keyboard/ARIA/content/placement/cleanup tests passed after the
   minimal popover implementation.
5. The CSS test failed before viewport-safe and focus-visible popover styles
   were added, then passed.
6. A payload-driven group-localization assertion failed while group metadata
   still used only stable keys; passing the full group entity into the existing
   fallback chain made it pass.
7. The first full warning-strict run exposed the expected closed-schema API
   assertion for factor groups; it now validates the new `i18n` member.

## Verification

- `../../venv/bin/python -m unittest tests.test_web_factors tests.test_web_assets -v`
  - PASS (47 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (129 tests)
- `for file in web/static/js/*.js; do node --check "$file"; done`
  - PASS
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-pycache ../../venv/bin/python -m py_compile web/factors/*.py`
  - PASS
- `git diff --check`
  - PASS

## Review notes

Self-review covered immutable metadata copying, optional legacy serialization,
payload/stable-key/server-text fallback order, safe DOM construction, duplicate
popover prevention, trigger state cleanup, keyboard activation, and viewport
clamping. The worktree has no local `./venv`, so verification used the
repository virtual environment at `../../venv`.

The independent review identified that the initial noninteractive popover used
`role="dialog"` without dialog focus management. A new RED assertion required
the appropriate tooltip/disclosure relationship; the popover now uses
`role="tooltip"`, and every trigger references it with `aria-describedby` while
retaining the required `aria-controls` and `aria-expanded` state.
