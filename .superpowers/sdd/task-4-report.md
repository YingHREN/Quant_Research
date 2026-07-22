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

## Review fix pass (2026-07-22)

Resolved all three Task 4 re-review findings:

- The popover now records whether it opened from hover, focus, click, or
  keyboard activation. Escape restores trigger focus only for click/keyboard
  activation, and focus restoration is guarded so its synchronous focus event
  cannot immediately reopen the popover.
- Trigger and popover hover state is coordinated with a 100 ms close delay.
  Entering either surface cancels the pending close; leaving both closes the
  hover disclosure, while click/keyboard-pinned disclosures remain open until
  explicitly toggled, escaped, or dismissed outside.
- `FactorResult.window` and `FactorResult.i18n` now follow the pre-existing
  `percentile`, `peer_count`, and `display_score` fields, preserving the legacy
  positional constructor contract while keeping all current keyword callers
  unchanged.

### Review-fix TDD evidence

The focused RED run was:

```text
../../venv/bin/python -m unittest \
  tests.test_web_factors.FactorRegistryTest.test_result_preserves_legacy_optional_positional_arguments \
  tests.test_web_assets.WebAssetTest.test_factor_popover_supports_pointer_keyboard_aria_and_cleanup -v

FAILED (errors=2)
- FactorResult treated legacy peer_count=12 as i18n and raised AttributeError.
- The JavaScript interaction process failed when the fake focus implementation
  dispatched a real focus event and the new hover-transfer assertions expected
  delayed coordinated closure.
```

After the minimal implementation, the same command passed both regressions.
The JavaScript regression now proves that Escape on a hover-opened popover does
not focus the trigger or reopen the popover; it also deterministically drains
fake timers to cover trigger-to-popover movement, leaving both surfaces, and
click-pinned behavior. The Python regression passes all 16 legacy positional
arguments and verifies the percentile fields remain correctly assigned.

### Review-fix verification

- `../../venv/bin/python -m unittest tests.test_web_factors tests.test_web_assets -v`
  - PASS (48 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-review-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (130 tests)
- `node --check` for every file in `web/static/js/*.js`
  - PASS (9 files)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-review-pycache ../../venv/bin/python -m py_compile web/factors/*.py`
  - PASS
- `git diff --check`
  - PASS

No unresolved Task 4 review concern remains.

### Independent fix review follow-up

The independent read-only review found one additional Important interaction
edge case: focus and hover were each correct in isolation, but were not tracked
independently, so a popover leave could close a still-focused disclosure and a
blur could close a still-hovered disclosure.

Two mixed-modality assertions were added first. The focused JavaScript test
failed before the implementation changed. Per-trigger focus/pointer presence
is now tracked independently from popover pointer presence, and unpinned
closure occurs only when none of those states remains. The same focused test
then passed, including both focus-plus-pointer transfer and hover-plus-blur
sequences.

The independent re-review reported no Critical, Important, or Minor findings
and assessed the updated diff ready. Fresh post-review verification repeated
the 48-test focused suite, 130-test warning-strict full suite, all nine
JavaScript syntax checks, Python compilation, and `git diff --check`; all
passed.

### Cross-trigger mixed-modality fix

A final re-review identified that presence arbitration only considered the
active trigger. Two RED sequences demonstrated the gap:

- focus trigger A, hover trigger B, then leave B: the popover closed instead of
  restoring A's explanation;
- hover trigger A, focus trigger B, then blur B: the popover closed instead of
  restoring A's explanation.

The trigger-presence registry now retains each trigger's explanation and a
monotonic last-interaction sequence. When the active unpinned trigger loses its
last presence, the singleton popover deterministically selects the most recent
remaining focused/hovered trigger. Pointer transitions still use the close
delay so entering the popover can cancel arbitration; blur can arbitrate
immediately. Pinned, Escape, outside-click, and rerender-close paths remain
explicit dismissals, and rerender clears the iterable trigger registry before
registering the new DOM.

The focused interaction test failed before this change and passed after it,
including explanation-content and `aria-expanded` assertions for both
cross-trigger sequences.

Fresh verification for this final pass:

- `../../venv/bin/python -m unittest tests.test_web_factors tests.test_web_assets -v`
  - PASS (48 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-cross-trigger-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (130 tests)
- `node --check` for every file in `web/static/js/*.js`
  - PASS (9 files)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-cross-trigger-pycache ../../venv/bin/python -m py_compile web/factors/*.py`
  - PASS
- `git diff --check`
  - PASS

The independent final re-review found no Critical, Important, or Minor issues
and assessed the cross-trigger fix ready.

### Explicit-dismissal suppression fix

The final cross-trigger review exposed a separate dismissal rule: a trigger
that remains logically focused or hovered after explicit dismissal must not be
selected again merely because another trigger temporarily becomes active.

Two new assertions failed before the implementation changed:

- focus A, press Escape, hover and leave B: A reopened without a new A
  interaction;
- focus/click A, activate A again to close it, then hover and leave B: A
  reopened from its stale focus presence.

Each registered trigger now carries explicit suppression state. Escape,
outside-click, and activation-toggle closure suppress the dismissed trigger;
last-interaction arbitration skips suppressed candidates. Suppression clears
only on that trigger's next pointer-enter, unsuppressed focus event, or
activation. Programmatic focus restoration during Escape remains guarded and
does not count as a new opening interaction. The registry and all suppression
state are cleared on rerender.

The focused interaction test passed after the minimal state change and also
proves that a subsequent real focus transition or pointer-enter on A clears
suppression and opens A normally.

Fresh verification for the dismissal-suppression pass:

- `../../venv/bin/python -m unittest tests.test_web_factors tests.test_web_assets -v`
  - PASS (48 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-dismissal-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (130 tests)
- `node --check` for every file in `web/static/js/*.js`
  - PASS (9 files)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task4-dismissal-pycache ../../venv/bin/python -m py_compile web/factors/*.py`
  - PASS
- `git diff --check`
  - PASS

The independent final re-review found no Critical, Important, or Minor issues
and assessed the explicit-dismissal suppression fix ready.
