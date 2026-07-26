"""Versioned point-in-time provenance for forecast input features."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
import re
from zoneinfo import ZoneInfo

import pandas as pd

from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS


REGISTRY_VERSION = "feature_provenance_registry_v1"
FEATURE_VERSION = "ridge-features-v2"
MARKET_TIMEZONE = ZoneInfo("America/New_York")
CONTENT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    source: str
    availability: str
    execution_timing: str

    def __post_init__(self):
        for field_name in (
            "key",
            "source",
            "availability",
            "execution_timing",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")


class FeatureProvenanceRegistry:
    """Immutable feature catalog plus validated point-in-time snapshots."""

    def __init__(self, version, feature_version, features):
        self.version = _required_text(version, "version")
        self.feature_version = _required_text(
            feature_version,
            "feature_version",
        )
        self._features = tuple(features)
        if not all(
            isinstance(feature, FeatureDefinition)
            for feature in self._features
        ):
            raise TypeError("features must contain FeatureDefinition values")
        keys = [feature.key for feature in self._features]
        if len(keys) != len(set(keys)):
            raise ValueError("feature keys must be unique")

    @property
    def features(self):
        return self._features

    def public_contract(self):
        return {
            "version": self.version,
            "feature_version": self.feature_version,
            "features": [asdict(feature) for feature in self._features],
        }

    def snapshot(
        self,
        observed_through,
        data_version,
        *,
        source_cutoff=None,
        available_at=None,
    ):
        observed = _normalized_date(observed_through, "observed_through")
        cutoff = _normalized_date(
            observed if source_cutoff is None else source_cutoff,
            "source_cutoff",
        )
        if cutoff > observed:
            raise ValueError("source_cutoff must not exceed observed_through")
        if not isinstance(data_version, str) or not CONTENT_HASH_RE.fullmatch(
            data_version
        ):
            raise ValueError("data_version must be a 64-character hex hash")
        available = (
            datetime.combine(
                observed.date(),
                time(16, 0),
                tzinfo=MARKET_TIMEZONE,
            )
            if available_at is None
            else _available_datetime(available_at)
        )
        local_available = available.astimezone(MARKET_TIMEZONE)
        if local_available.date() != observed.date():
            raise ValueError(
                "available_at must be on the observed market session"
            )
        if local_available.timetz().replace(tzinfo=None) < time(16, 0):
            raise ValueError(
                "daily close features cannot be available before 16:00 ET"
            )
        return {
            "registry_ref": self.version,
            "feature_version": self.feature_version,
            "observed_through": observed.date().isoformat(),
            "source_cutoff": cutoff.date().isoformat(),
            "available_at": available.isoformat(),
            "data_version": data_version,
            "execution_timing": "next_session_open",
        }


def default_feature_provenance_registry():
    return _DEFAULT_FEATURE_PROVENANCE_REGISTRY


def _default_registry():
    pressure_features = {
        feature
        for feature in RIDGE_V4_FEATURE_COLUMNS
        if feature.startswith("pressure_")
    }
    market_features = {
        feature
        for feature in RIDGE_V4_FEATURE_COLUMNS
        if feature.startswith("qqq_")
    }
    sector_features = {
        "sector_relative_strength_20",
        "stock_sector_relative_strength_20",
    }
    features = []
    for key in RIDGE_V4_FEATURE_COLUMNS:
        if key in pressure_features:
            source = "daily_ohlcv_pressure_proxy"
        elif key in market_features:
            source = "qqq_daily_ohlcv"
        elif key in sector_features:
            source = "sector_etf_daily_ohlcv"
        else:
            source = "stock_daily_ohlcv"
        features.append(
            FeatureDefinition(
                key=key,
                source=source,
                availability="session_close",
                execution_timing="next_session_open",
            )
        )
    return FeatureProvenanceRegistry(
        REGISTRY_VERSION,
        FEATURE_VERSION,
        features,
    )


def _required_text(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalized_date(value, field_name):
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid date") from exc
    if pd.isna(result):
        raise ValueError(f"{field_name} must be a valid date")
    if result.tz is not None:
        result = result.tz_convert(MARKET_TIMEZONE).tz_localize(None)
    return result.normalize()


def _available_datetime(value):
    try:
        result = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("available_at must be an ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("available_at must include a UTC offset")
    return result


_DEFAULT_FEATURE_PROVENANCE_REGISTRY = _default_registry()
