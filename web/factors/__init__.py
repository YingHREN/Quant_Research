"""Extensible dashboard factor definitions and evaluation registry."""

from web.factors.base import FactorDefinition, FactorResult
from web.factors.registry import DuplicateFactorKey, FactorRegistry

__all__ = [
    "DuplicateFactorKey",
    "FactorDefinition",
    "FactorRegistry",
    "FactorResult",
]
