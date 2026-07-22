"""Ordered, duplicate-safe registration for forecast providers."""

from __future__ import annotations


class DuplicateForecastProviderKey(ValueError):
    """Raised when registration would overwrite an existing provider."""


class ForecastRegistry:
    """Ordered collection of uniquely keyed forecast providers."""

    def __init__(self, providers=()):
        self._providers = {}
        for provider in providers:
            self.register(provider)

    @property
    def providers(self):
        return tuple(self._providers.values())

    def register(self, provider):
        key = getattr(provider, "model_key", None)
        if not isinstance(key, str):
            raise TypeError("forecast provider model_key must be a string")
        key = key.strip()
        if not key:
            raise ValueError("forecast provider model_key must not be empty")
        if key in self._providers:
            raise DuplicateForecastProviderKey(
                f"Forecast provider key already registered: {key}"
            )
        self._providers[key] = provider
        return provider

    def get(self, model_key):
        return self._providers[model_key]
