# Hide the Model Forecast Line

## Goal

Stop drawing the blue model forecast projection that extends from a selected historical date into future sessions.

## Retained behavior

The forecast remains available as research data. Selecting a historical date will continue to show:

- The selected observation marker and date.
- The model direction and forecast horizon.
- Predicted return and model provenance.
- Upside-strengthening and downside-acceleration evidence.
- Click-to-lock and hover inspection behavior.

Price candles, volume, moving averages, structural trend evidence, key levels, and range controls are unchanged.

## Implementation

Keep the existing forecast projection series as an internal chart series but configure it as not visible. This is the smallest reversible change: forecast data flow and interaction code remain intact, while Lightweight Charts does not render the line or its right-axis title.

The hidden series must continue to opt out of autoscaling so forecast values cannot alter chart layout.

## Verification

Automated tests will assert that the forecast projection series:

- Is configured with `visible: false`.
- Retains `autoscaleInfoProvider: () => null`.
- Continues to receive its point-in-time projection data.

Browser verification will select a historical NBIS date and confirm that forecast details appear without a blue model forecast line or its axis label.

