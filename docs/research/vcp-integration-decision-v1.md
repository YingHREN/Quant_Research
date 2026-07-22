# VCP Integration Decision v1

## Decision

Keep the new VCP detector and its fields in research and descriptive monitoring
only. Do not integrate it into portfolio selection or describe it as a validated
buy signal.

## Evidence

The v1 event table contains 328 unique near-pivot events across 127 tickers and
164 independent observation dates from 2025-07-24 through 2026-07-17.

On the preregistered primary 40-day SPY-relative outcome:

- VCP-only mean fold correlation: -0.026;
- VCP-only mean out-of-sample R-squared: -0.580;
- VCP-plus-momentum mean fold correlation: +0.004;
- VCP-plus-momentum mean out-of-sample R-squared: -0.380;
- the combined model's date-block interval for MSE improvement is entirely
  negative.

These results do not support VCP structure as an incremental predictor. The
sample also lacks the history, matched controls, sector metadata, and independent
market regimes required for a conclusive test, so the formal verdict is
`UNDERPOWERED`, not `PASS`.

The momentum-only model is a follow-up research hypothesis, not an integration
candidate. It produced positive mean fold correlations at 40 and 60 days, but
the primary 40-day block-bootstrap interval crosses zero. The most stable
individual coefficient was `mom_12_1_rank` (positive in 4/4 training folds).
`vol_adjusted_mom_6_1` was negative in 4/4 folds. Both require longer data and
the missing validation gates.

## Dashboard direction model evidence

The dashboard's `ridge_direction_v1` output is a separate research aid and does
not change this VCP decision. Its 5-, 20-, and 60-session targets are absolute
close-to-close returns, not the 40-day SPY-relative primary outcome above. The
three classes use fixed version-one neutral bands of ±1%, ±2%, and ±4%,
respectively. A direction, predicted return, or evaluation metric is not a
claim of validated alpha, profitability, or suitability for live trading.

The evaluation is expanding-window and point-in-time. Features for an
observation date use only that date and its past. Forward targets are created by
`attach_forward_targets(build_feature_frame(histories))`, and every target
carries an explicit `label_end_date`. For a forecast at date `t`, a training row
is eligible only when its `label_end_date` is strictly before `t`; a label that
ends on `t` is excluded. The historical-mean baseline uses the same boundary.
Preprocessing is refit on each eligible training set, and calibration may use
only earlier out-of-sample predictions whose own outcomes have matured.

Exhaustive walk-forward summaries are an explicit offline research job, not
request-time work. The production stock API returns the full evaluation
contract with typed `not_precomputed` values until revision/model-specific
evidence has been produced and integrated by a separate ingestion path. The
command below reports evidence but does not populate the production API
automatically. To run the exhaustive evaluation from the same local database,
use the following command from the repository root. It performs hundreds of
thousands of expanding-window fits on the current dataset and can take
substantially longer than the unit tests:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python - <<'PY'
import json

from web.forecasts.base import SUPPORTED_HORIZONS
from web.forecasts.dataset import attach_forward_targets, build_feature_frame
from web.forecasts.evaluation import walk_forward_evaluate
from web.forecasts.ridge import RidgeForecastProvider
from web.services.market_data import MarketDataRepository

repository = MarketDataRepository("data/prices.db")
histories = repository.load_universe_histories()
frame = attach_forward_targets(build_feature_frame(histories))
provider = RidgeForecastProvider(frame)

for horizon in SUPPORTED_HORIZONS:
    result = walk_forward_evaluate(frame, horizon, provider)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
PY
```

Interpret the fields conservatively:

- `coverage` is available forecasts divided by all structurally valid rows with
  realized targets; it is not accuracy.
- MAE and RMSE measure return-prediction error on available forecast rows. The
  zero-return baseline and expanding historical-mean baseline use those same
  rows, so compare errors on a like-for-like basis.
- `direction accuracy` is the share of the three fixed-band classes predicted
  correctly. Class imbalance can make this look useful when return errors are
  not, so inspect it alongside both baselines and coverage.
- `rank IC` is the mean per-date Spearman correlation between predicted and
  realized returns. A date contributes only with at least five names and usable
  cross-sectional variation.
- `signal-bucket returns` are realized mean returns for `down`, `neutral`, and
  `up` predictions, again only on dates meeting the cross-sectional threshold.
  They are descriptive and have no uncertainty estimate in this report.

Do not substitute a bounded sample or a latest-date fit for this full
evaluation. If the offline job has not completed, preserve `not_precomputed`
instead of publishing partial metrics as full evidence.

Short history, survivorship in the local ticker universe, overlapping forward
outcomes, regime concentration, repeated use of the same data for inspection,
and absent transaction costs all limit interpretation. Probability is omitted
unless at least 100 matured earlier out-of-sample rows contain both outcomes of
the horizon-specific `up` event. A realized return must exceed +1%, +2%, or +4%
at 5, 20, or 60 sessions to count as up; a positive return inside the neutral
band is non-up. A missing probability is evidence that the calibration gate did
not pass, not a zero-probability forecast.

## Known legacy conflict

`scoring/engine.py` currently:

- allocates up to 20 points to VCP structure;
- rescales those points upward in `price_only=True` mode;
- emits `vcp_breakout` and `buyable_now` triggers;
- feeds the resulting score to `engine_v2.py` selection.

Characterization tests preserve this legacy behavior so that later changes are
explicit. This iteration does not silently change historical backtest behavior.

## Required follow-up before changing the legacy engine

1. Build point-in-time daily snapshots for same-ticker, same-regime controls.
2. Extend the price history to at least ten years and use a point-in-time universe.
3. Re-run the frozen v1 definitions without tuning on the new history.
4. If VCP remains unsupported, remove VCP points and `buyable_now` language from
   new selection paths in a separately approved change.
5. If momentum survives every gate, introduce it in shadow mode before allocating
   capital.
