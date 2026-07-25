# Unified downside-risk evaluation — 2026-07-25

## Scope and information boundary

This evaluation uses the local daily OHLCV database through 2026-07-23. Every
risk score is built first from observations available through the score date.
Only afterward are the next five observed sessions attached as labels. The
adverse outcome is a next-five-session low at least 5% below the signal-date
close. The procedure is reproducible with:

```bash
./venv/bin/python -m research.evaluate_unified_risk \
  --database data/prices.db
```

The three source-specific high thresholds are versioned as:

- individual 12-rule remembered risk: 30/100;
- group breadth/volume stress: 60/100;
- persistent slow decline: 70/100.

The different thresholds are intentional. The three scores have different
distributions and cannot be treated as interchangeable probabilities.

## Full available history

| Source | Samples | Signal rate | Precision | Recall | Balanced accuracy | Mean future return | Mean future MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| Individual | 18,861 | 10.04% | 42.03% | 11.39% | 51.07% | +1.25% | -5.24% |
| Group stress | 18,861 | 13.04% | 42.21% | 14.85% | 51.44% | +1.22% | -5.32% |
| Slow decline | 18,861 | 28.15% | 39.34% | 29.89% | 51.38% | +0.68% | -5.05% |
| Any high source | 18,861 | 37.61% | 40.20% | 40.80% | 52.53% | +0.69% | -5.14% |

These results do not demonstrate a standalone directional forecasting edge.
The sources remain risk-state evidence. A high persistent source downgrades a
raw bullish Ridge direction to neutral; it does not independently force a
down forecast. A down override still requires same-date bearish confirmation
or persistent/immediate confluence.

## 2026-only stability check

The any-high policy has 49.4% adverse-outcome precision, 46.3% recall, and
51.9% balanced accuracy over 5,092 matured rows. This remains too weak for a
probability claim or autonomous short signal. The result supports keeping the
asymmetric, conservative decision policy.

## Named event regressions

On 2026-06-26, broad semiconductor stress reaches 100/100. The unified
five-session decisions become:

- MU: raw up → down because group/individual risk and immediate score 57
  agree; realized five-session return -13.02%.
- INTC: raw up → neutral from group stress; realized -4.77%.
- NBIS: raw up → neutral from group stress; realized -11.35%.
- MRVL: raw up → neutral from group stress; realized -6.56%.

On 2026-07-01:

- NBIS: raw up → down because the immediate eight-condition score is 100;
  realized five-session return -5.66%.
- MU, INTC, and MRVL: raw up → neutral because remembered group or individual
  risk remains high; realized returns are -3.92%, -11.40%, and -10.58%.

ADBE illustrates why slow-decline risk is not a direct down forecast. Its
slow-decline state is high during late June, while its next-five-session return
from 2026-06-25 is +13.60%. The UI therefore shows the high persistent erosion
state beside the unchanged neutral Ridge conclusion instead of fabricating a
down prediction.

## Decision

Retain all three sources as separately named, causal risk states. Use
source-specific thresholds and a 5-session half-life / 10-session memory.
Do not market any score as probability. Keep Ridge as the return model, use
high persistent risk to veto bullish presentation to neutral, and require
same-date confirmation before displaying down.
