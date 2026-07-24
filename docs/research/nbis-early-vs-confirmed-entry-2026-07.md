# NBIS Early Observation vs Structural Confirmation

Observation data are current through 2026-07-23. Both entries use the next
available session open; no signal-day close is treated as executable.

| Signal | Signal date | Entry date | Entry price | Return through 2026-07-23 |
| --- | --- | --- | ---: | ---: |
| Early reversal watch | 2026-07-17 | 2026-07-20 | 186.965 | +18.19% |
| Trendline-breakout confirmation | 2026-07-20 | 2026-07-21 | 193.050 | +14.46% |

Waiting for structural confirmation delayed entry by one session and raised
the executable entry price by 3.25%. This is one inspected case, not evidence
that early entry is generally superior.

The 5- and 20-session outcomes are both explicitly unavailable because the
local history ends on 2026-07-23. They must remain unavailable until the
required exit sessions exist. The reusable
`research.early_reversal.compare_next_open_entries` function recomputes the
comparison without forward filling missing bars.
