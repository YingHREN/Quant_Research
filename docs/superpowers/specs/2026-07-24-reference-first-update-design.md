# Reference-First Market Update Design

## Goal

Make the market and sector dashboard useful early in a full price update by
fetching its fixed benchmark universe before ordinary active stocks.

## Behavior

`UpdateJobManager` will build one deterministic, duplicate-free work list in
this order:

1. fixed reference tickers supplied to the manager;
2. locally active stock tickers not already present.

The total update universe does not change. Existing rate-limit, partial-failure,
cache-invalidation, progress, and resumable-update behavior remains unchanged.
If a reference ticker also appears in the active universe, it is fetched once
at its reference-priority position.

No new route, button, provider request type, or automatic page-triggered update
is introduced.

## Data Flow

The application continues to pass `REFERENCE_TICKERS` into
`UpdateJobManager`. When a new non-resumed run starts, the manager loads active
summaries, filters inactive rows, and combines the two sources with stable
deduplication. Resumed jobs preserve their existing remaining-list order.

## Error Handling

Reference tickers use the same per-ticker transaction and error handling as all
other symbols. A provider failure advances to the next ticker and produces a
partial terminal state; HTTP 429 preserves the remaining list for resume.

## Verification

Automated tests will prove that:

- references precede active tickers;
- duplicates are fetched once;
- active-only behavior remains deterministic;
- existing full-suite update semantics still pass.

Manual verification will restart the local server, start one update, and check
that the first reported/current symbols belong to the reference pool.
