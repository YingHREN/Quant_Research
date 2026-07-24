# Intraday Foundation Final Fix Report

Date: 2026-07-24

Reviewed base: `ff20e4a68c5a9300486b8cc43fead1489784555e`

Implementation commits:

- `722af83` — `fix: harden intraday event and subscription truth`
- `8cf0a2c` — `fix: persist collector state and batch event writes`

No design, implementation-plan, or progress files were modified.

## Outcome

All nine Important findings and the requested minor hardening were addressed.
The implementation remains limited to the planned Alpaca IEX real-time
foundation: it adds no historical-bars client, no real network calls in tests,
and no unrelated scoring or dashboard feature work.

## Finding-by-finding TDD evidence

### 1. Subscription ACK is the source of truth

RED:

- Added fake-socket counterexamples for delayed authentication, delayed initial
  acknowledgement, partial initial/update acknowledgements, error
  acknowledgements, and acknowledgement ordering.
- Added a collector counterexample proving that the old collector published
  `running` and opened subscription intervals before provider confirmation.
- Added a stale-session counterexample proving that prior open intervals, and
  even an orphaned interval from another old session, remained live.

GREEN:

- `AlpacaIEXProvider` now authenticates first, validates the complete trade and
  quote sets in every subscription ACK, and returns an immutable
  `SubscriptionConfirmation`.
- Stable safe protocol codes are used for authentication, subscription, and
  mismatch failures; provider details and credentials are not exposed.
- Updates wait for the additions ACK before sending removals, then validate the
  final ACK.
- `IntradayCollector` publishes `running`, updates its active set, and opens or
  closes intervals only after confirmation.
- Subscription intervals carry a collector session id. Starting a new session
  rejects a fresh active owner and closes all stale/orphaned open intervals
  before publishing the new session.

Commits: `722af83`, `8cf0a2c`

Key tests:

- `test_initial_confirmation_waits_for_delayed_auth_and_exact_ack`
- `test_partial_initial_ack_fails_with_fixed_safe_error`
- `test_partial_update_ack_fails_without_claiming_desired_set`
- `test_subscription_error_ack_has_fixed_safe_error`
- `test_update_waits_for_add_ack_before_remove_and_returns_confirmation`
- `test_collector_waits_for_provider_confirmation_before_running_or_opening`
- `test_new_session_closes_stale_crashed_session_intervals`

### 2. Pre-connect failure cannot retain an old desired pool

RED:

- Added the AMD pre-connect failure followed by an NVDA retry counterexample;
  the retry previously subscribed the stale AMD pool.

GREEN:

- Each stream ownership generation is initialized from that call's request.
- Desired-symbol updates remain last-write-wins within the current generation.
- Every stream exit path clears connection-generation state without erasing a
  newer update made during that generation.

Commit: `722af83`

Key test:

- `test_preconnect_failure_does_not_leak_old_desired_pool_to_retry`

### 3. Exact provider nanoseconds are preserved

RED:

- Added two otherwise-identical quotes 800 ns apart; the preceding
  microsecond-only identity stored one row instead of two.
- Added schema-migration coverage against the immediately preceding intraday
  schema while retaining an existing `prices` row.

GREEN:

- Market events carry validated integer Unix `event_ts_ns` while retaining UTC
  `datetime` convenience values.
- RFC3339 parsing preserves all nine fractional digits.
- Trade/quote rows persist `event_ts_ns` and the explicit US/Eastern
  `trading_date`.
- Fallback identities use the exact timestamp plus the full normalized payload.
  Exact payload replays intentionally remain idempotent, so an occurrence index
  is not needed; events that differ by 800 ns coexist.
- Initialization migrates preceding intraday tables in place and does not alter
  `prices`.

Commit: `722af83`

Key tests:

- `test_nanosecond_timestamp_is_preserved_alongside_utc_datetime`
- `test_quotes_separated_only_by_800_nanoseconds_both_persist`
- `test_initialize_migrates_preceding_intraday_schema_without_touching_prices`

### 4. Direction inference is causal and monotonic

RED:

- Added future-quote, stale-quote, out-of-order-trade, and normal in-order
  counterexamples. The preceding normalizer used a future/stale midpoint and
  allowed a late trade to regress tick-rule state.

GREEN:

- Quote and previous-trade state are timestamped in nanoseconds.
- Midpoint classification is permitted only for
  `quote_ts <= trade_ts` and within
  `QUOTE_MID_MAX_AGE_NS_V1`.
- Future/stale quotes fall back safely; late trades cannot move prior-trade
  state backward.
- Quality counters expose `future_quote`, `stale_quote`, and
  `out_of_order_trade`.

Commit: `722af83`

Key tests:

- `test_future_quote_falls_back_without_using_future_information`
- `test_stale_quote_falls_back_to_tick_rule`
- `test_out_of_order_trade_never_regresses_tick_rule_state`
- `test_quote_then_trade_uses_contemporaneous_midpoint`

### 5. CLI collector and Flask status share persisted state

RED:

- Added separate writer/Flask-reader lifecycle tests. The default Flask service
  previously always returned `collector_not_configured`.
- Added stale heartbeat and stale-session tests.
- Added a heartbeat snapshot counterexample; the in-process snapshot originally
  did not expose the timestamp actually persisted.

GREEN:

- SQLite now holds a singleton collector snapshot with session, provider,
  coverage, lifecycle state, confirmed symbols, last received event,
  disconnect count, safe error code, heartbeat, and queue health.
- Lifecycle transitions and periodic heartbeats persist through transactional
  store calls.
- The default Flask status service opens the configured database read-only and
  never starts a collector or network connection.
- Fixed states/codes distinguish running, stale/crashed, stopped,
  `missing_credentials`, and `collector_not_configured`.
- CLI and Flask share one absolute project-root default database path.

Commit: `8cf0a2c`

Key tests:

- `test_persisted_collector_status_distinguishes_stale_and_never_configured`
- `test_separate_store_writer_and_default_flask_reader_share_lifecycle`
- `test_persisted_missing_credentials_is_distinct_from_never_configured`
- `test_unavailable_provider_records_capability_without_connecting`
- `test_cli_and_flask_share_absolute_project_database_default`

### 6. Quote sizes are normalized to shares

RED:

- Added an Alpaca quote with `bs=3`; the preceding event represented it as
  three without explicit unit/provenance.

GREEN:

- Alpaca quote sizes are multiplied by the 100-share round-lot size.
- Quote events and storage explicitly retain `size_unit="shares"` and
  `lot_size=100`; trades retain share units independently.
- Migration normalizes preceding Alpaca quote rows once when the provenance
  columns are introduced.

Commit: `722af83`

Key tests:

- `test_alpaca_quote_round_lots_are_normalized_to_shares`
- `test_quote_size_unit_and_lot_provenance_are_persisted`

### 7. Trade corrections and cancels are retained and replayable

RED:

- Added normal trade → correction and normal trade → cancel cases; the
  preceding normalizer ignored both message types and storage had no contracts
  or replay path.
- Added replayed adjustments with a different local receive timestamp; the
  first identity attempt incorrectly inserted duplicates.
- Added a shared provider-trade id across symbols; the first replay helper
  incorrectly applied AMD's correction to NVDA.

GREEN:

- Immutable `TradeCorrectionEvent` and `TradeCancelEvent` contracts carry
  provider identity, exact timestamp, replacement/cancel metadata, session, and
  local trading date.
- Normalization handles Alpaca `c` and `x` explicitly.
- Raw trades and adjustments remain immutable in separate tables; exact
  adjustment replays are idempotent independent of local receive time.
- `read_effective_trades()` applies corrections/cancels with a scoped
  `(provider, symbol, provider_trade_id)` identity.

Commits: `722af83`, `8cf0a2c`

Key tests:

- `test_trade_correction_and_cancel_messages_are_normalized`
- `test_trade_correction_and_cancel_preserve_raw_and_replay_effective_trades`
- `test_effective_trade_adjustments_are_scoped_by_provider_and_symbol`

### 8. Historical bars are not advertised

RED:

- Added a capability regression test; `historical_bars` was true without a
  fetch method.

GREEN:

- Alpaca IEX now reports `historical_bars=False`.
- No HTTP bars client was added.

Commit: `722af83`

Key test:

- `test_capabilities_do_not_advertise_unimplemented_historical_bars`

### 9. WebSocket consumption is decoupled from SQLite commits

RED:

- Added burst, bounded-queue, queue-full, batch-write, storage-error, startup
  storage-error, and shutdown-drain counterexamples.
- The preceding sink committed synchronously for each event, had no queue
  health, and routed storage failure through provider-disconnect handling.

GREEN:

- The collector owns a bounded `asyncio.Queue` and one writer worker.
- Event delivery uses lossless `await queue.put` backpressure; no event is
  silently dropped.
- The worker writes bounded batches through `IntradayStore.write_events()` in
  `asyncio.to_thread`, keeping SQLite work off the WebSocket event loop.
- Status exposes current depth, high-water mark, dropped count (zero under the
  lossless policy), and undrained count.
- Startup/write/status storage failures set safe
  `state="collector_error", error="storage_error"`, stop collection, and do not
  increment provider disconnect count.
- Normal shutdown disables new delivery, drains queued events, closes active
  intervals and the provider, and records zero undrained events.

Commit: `8cf0a2c`

Key tests:

- `test_burst_events_use_bounded_queue_and_batch_storage_api`
- `test_queue_full_applies_lossless_backpressure`
- `test_storage_error_is_typed_and_not_counted_as_provider_disconnect`
- `test_startup_storage_error_is_typed_without_connect_or_disconnect`
- `test_shutdown_waits_for_queue_drain`

## Minor hardening

- Provider-neutral trade, quote, correction, cancel, and bar numeric fields
  reject NaN/Inf and nonnumeric values.
- `BarEvent` validates UTC/order, interval shape, finite positive OHLC/VWAP,
  OHLC consistency, nonnegative finite volume, and nonnegative integer count.
- CLI and Flask use the same absolute project-root database default.

Commits: `722af83`, `8cf0a2c`

## Verification

Interpreter:

`/Users/renyinghao.1/Project/stock_screener/venv/bin/python`

Results at implementation head `8cf0a2c`:

- Focused intraday suites:
  `python -m unittest tests.test_marketdata_contracts
  tests.test_marketdata_normalization tests.test_marketdata_storage
  tests.test_marketdata_subscriptions tests.test_marketdata_alpaca
  tests.test_marketdata_collector tests.test_web_intraday_status
  tests.test_collect_intraday -v`
  — **99 passed**
- Complete suite:
  `python -m unittest discover -s tests -v`
  — **321 passed**
- Repository Python compilation with
  `PYTHONPYCACHEPREFIX=/private/tmp/intraday-foundation-final-pycache`
  — **passed**
- `git diff --check`
  — **passed**

All network/provider tests use fakes; no real network request was made.

## Remaining concerns

- No correctness blocker is known.
- Live Alpaca behavior still depends on the external service honoring its
  documented IEX WebSocket protocol; this wave deliberately validates it with
  deterministic fake sockets rather than credentials or real network access.
- The status record intentionally represents the one locally configured
  collector owner. Multi-host leader election is outside this foundation's
  scope; fresh-session fencing prevents a second local owner and stale-session
  takeover repairs prior open intervals.
