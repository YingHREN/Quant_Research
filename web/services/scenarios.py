"""Point-in-time historical return distributions for dashboard scenarios."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from web.contracts import iso_date


MINIMUM_SAMPLES = 8
VOLATILITY_WINDOW = 63
TRADING_DAYS_PER_YEAR = 252
PROVIDER_NAME = "historical_distribution"


class HistoricalScenarioProvider:
    """Build descriptive paths from non-overlapping historical returns.

    This provider deliberately uses only bars known at ``asof``.  Its paths are
    historical distribution summaries, not forecasts, targets, or probabilities.
    """

    def __init__(self, horizons=(20, 40, 60), quantiles=(0.25, 0.5, 0.75)):
        self.horizons = tuple(horizons)
        self.quantiles = tuple(quantiles)
        if not self.horizons or any(not isinstance(value, int) or value <= 0 for value in self.horizons):
            raise ValueError("Horizons must be positive integer session counts")
        if (
            len(self.quantiles) != 3
            or any(not 0 <= value <= 1 for value in self.quantiles)
            or tuple(sorted(self.quantiles)) != self.quantiles
        ):
            raise ValueError("Quantiles must be three ordered values between zero and one")

    def build(self, history, asof=None):
        """Return scenario bands using adjusted closes available by ``asof`` only."""
        close, missing_reason = self._close_asof(history, asof)
        observation_date = None if close.empty else iso_date(close.index[-1])
        observation_close = None if close.empty else float(close.iloc[-1])
        result = {
            "provider": PROVIDER_NAME,
            "observation_date": observation_date,
            "observation_close": observation_close,
            "methodology": (
                "Descriptive historical scenarios from non-overlapping horizon "
                "returns available at the observation date; not predictions or probabilities."
            ),
            "horizons": {},
        }

        for horizon in self.horizons:
            samples = self._non_overlapping_returns(close, horizon)
            if missing_reason is not None:
                result["horizons"][str(horizon)] = self._missing_band(
                    horizon, len(samples), missing_reason
                )
            elif len(samples) < MINIMUM_SAMPLES:
                result["horizons"][str(horizon)] = self._missing_band(
                    horizon, len(samples), "insufficient_samples"
                )
            else:
                result["horizons"][str(horizon)] = self._available_band(
                    horizon, samples, observation_close, close
                )
        return result

    @staticmethod
    def _close_asof(history, asof):
        if not isinstance(history, pd.DataFrame):
            return pd.Series(dtype=float), "invalid_history"
        column = "Adj Close" if "Adj Close" in history.columns else "Close"
        if column not in history.columns:
            return pd.Series(dtype=float), "missing_close"

        frame = history.loc[:, [column]].copy()
        frame.index = pd.to_datetime(frame.index, errors="coerce")
        frame = frame.loc[~frame.index.isna()].sort_index()
        if asof is not None:
            frame = frame.loc[frame.index <= pd.Timestamp(asof)]
        close = pd.to_numeric(frame[column], errors="coerce")
        if close.empty:
            return close.astype(float), "insufficient_history"
        if close.isna().any() or not np.isfinite(close.to_numpy(dtype=float)).all() or (close <= 0).any():
            return pd.Series(dtype=float), "invalid_close_history"
        return close.astype(float), None

    @staticmethod
    def _non_overlapping_returns(close, horizon):
        """Sample contiguous horizon returns backward in horizon-sized steps."""
        values = close.to_numpy(dtype=float)
        endpoints = range(len(values) - 1, horizon - 1, -horizon)
        return [float(values[end] / values[end - horizon] - 1) for end in endpoints]

    def _available_band(self, horizon, samples, observation_close, close):
        realized_volatility = self._realized_volatility(close)
        return_cap = float(
            3 * realized_volatility * math.sqrt(horizon / TRADING_DAYS_PER_YEAR)
        )
        raw_quantiles = np.quantile(samples, self.quantiles)
        capped_quantiles = np.clip(raw_quantiles, -return_cap, return_cap)
        names = ("pessimistic", "median", "optimistic")
        quantiles = {
            name: float(value) for name, value in zip(names, capped_quantiles)
        }
        return {
            "horizon_sessions": horizon,
            "available": True,
            "missing_reason": None,
            "sample_count": len(samples),
            "non_overlapping": True,
            "realized_vol_63": float(realized_volatility),
            "return_cap": return_cap,
            "quantiles": quantiles,
            "paths": {
                name: self._path(observation_close, value, horizon)
                for name, value in quantiles.items()
            },
            "methodology": (
                f"{len(samples)} non-overlapping {horizon}-session historical "
                f"returns, with absolute quantiles capped at three times current "
                "63-session realized-volatility scaling."
            ),
        }

    @staticmethod
    def _realized_volatility(close):
        daily_returns = close.pct_change().dropna().iloc[-VOLATILITY_WINDOW:]
        return float(daily_returns.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))

    @staticmethod
    def _path(observation_close, horizon_return, horizon):
        log_horizon_return = math.log1p(horizon_return)
        return [
            {
                "session": session,
                "return": float(math.expm1(log_horizon_return * session / horizon)),
                "price": float(
                    observation_close * math.exp(log_horizon_return * session / horizon)
                ),
            }
            for session in range(horizon + 1)
        ]

    @staticmethod
    def _missing_band(horizon, sample_count, reason):
        return {
            "horizon_sessions": horizon,
            "available": False,
            "missing_reason": reason,
            "sample_count": sample_count,
            "non_overlapping": True,
            "realized_vol_63": None,
            "return_cap": None,
            "quantiles": None,
            "paths": {},
            "methodology": (
                "No scenario is shown until enough point-in-time, non-overlapping "
                "historical return samples are available."
            ),
        }
