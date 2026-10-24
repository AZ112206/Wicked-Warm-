"""Provider interfaces for the Energy Cost Predictor.

All external data enters the core algorithm through these ``typing.Protocol``
interfaces. A provider is any object whose methods match the shape — no
inheritance required — which is what lets a real data source replace a
placeholder with zero changes to the core (Req 2.1-2.5).

The Protocols describe structure only; concrete placeholder implementations
(``PlaceholderWeatherProvider`` and friends) are defined elsewhere. This module
depends only on the standard library and :mod:`energy_cost_predictor.models`.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from .models import PriceTable, ReferenceActual, WeatherSeries

__all__ = [
    "WeatherProvider",
    "PricingProvider",
    "ReferenceActualProvider",
]


@runtime_checkable
class WeatherProvider(Protocol):
    """Source of per-interval outdoor temperatures for a region (Req 2.1)."""

    def get_temperatures(
        self, region_key: str, start: date, end: date
    ) -> WeatherSeries:
        """Return per-interval outdoor temperatures for ``[start, end]``.

        Missing intervals are represented by ``None`` in the series values,
        never silently dropped (supports Req 4.6, 2.6).
        """
        ...


@runtime_checkable
class PricingProvider(Protocol):
    """Source of energy unit prices for a region (Req 2.2)."""

    def get_prices(self, region_key: str) -> PriceTable:
        """Return unit prices keyed by fuel price key for the region
        (electricity $/kWh, gas $/therm, oil $/gal, propane $/gal)."""
        ...


@runtime_checkable
class ReferenceActualProvider(Protocol):
    """Source of a Reference_House's known actual annual cost (Req 2.3)."""

    def get_reference_actual(self, house_id: str) -> ReferenceActual:
        """Return the known actual annual heating cost for a reference
        house (Req 2.3, 8.6). Never hard-coded in the algorithm."""
        ...
