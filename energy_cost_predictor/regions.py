"""Region configuration and resolution for the Energy Cost Predictor.

This module holds the built-in :class:`RegionProfile` definitions — primarily
Massachusetts — and a small registry lookup, :func:`resolve_region`, that maps a
region key to its profile (Req 3.1, 3.3). The Massachusetts profile defines its
available heating fuels with price keys and per-unit energy content (Req 3.6), a
heat-pump COP curve that falls as outdoor temperature drops, the
:class:`SeasonalRateRule` covering November 1 through April 30 (Req 3.2), and an
informational heating-degree-day base temperature.

The module depends only on the data models and the typed error hierarchy; it
performs no I/O and imports no providers.
"""

from __future__ import annotations

from typing import Mapping, Optional

from .errors import MissingRegionKeyError, UnknownRegionError
from .models import COPCurve, FuelDef, RegionProfile, SeasonalRateRule

__all__ = [
    "MASSACHUSETTS_KEY",
    "MA_FUELS",
    "MA_COP_CURVE",
    "MA_SEASONAL_RATE",
    "MASSACHUSETTS",
    "REGION_REGISTRY",
    "resolve_region",
]

#: Canonical region key for the Massachusetts profile.
MASSACHUSETTS_KEY = "MA"


# ---------------------------------------------------------------------------
# Massachusetts fuels (Req 3.6)
# ---------------------------------------------------------------------------
#
# ``energy_content`` is the delivered-energy equivalent per fuel unit, expressed
# in kWh-thermal so the fuel-neutral demand (also kWh-thermal) converts cleanly
# to a physical quantity. The values below are standard physical conversions:
#   - natural gas : 1 therm   == 100,000 BTU  ~= 29.3 kWh
#   - heating oil : 1 gallon  == ~138,500 BTU ~= 40.6 kWh (No. 2 fuel oil)
#   - propane     : 1 gallon  == ~91,500 BTU  ~= 26.8 kWh
#   - electric    : 1 kWh     == 1 kWh (resistance heating is 1:1)
MA_FUELS: Mapping[str, FuelDef] = {
    "natural_gas": FuelDef(
        fuel_type="natural_gas",
        price_key="natural_gas_per_therm",
        energy_content=29.3,
        unit="therm",
    ),
    "heating_oil": FuelDef(
        fuel_type="heating_oil",
        price_key="heating_oil_per_gallon",
        energy_content=40.6,
        unit="gallon",
    ),
    "propane": FuelDef(
        fuel_type="propane",
        price_key="propane_per_gallon",
        energy_content=26.8,
        unit="gallon",
    ),
    "electric_resistance": FuelDef(
        fuel_type="electric_resistance",
        price_key="electricity_per_kwh",
        energy_content=1.0,
        unit="kWh",
    ),
}


# ---------------------------------------------------------------------------
# Massachusetts heat-pump COP curve
# ---------------------------------------------------------------------------
#
# Ascending-temperature breakpoints with COP monotonic non-decreasing as the
# outdoor temperature rises (Req 6.2, 6.3). These default values are drawn from
# NEEP's Cold Climate Air-Source Heat Pump Product List (neep.org) for a
# representative, widely deployed cold-climate ductless unit (Hyper-Heating
# class). NEEP publishes tested COP at the 5 F / 17 F / 47 F rating points, which
# map directly onto these breakpoints; the 35 F point is interpolated to keep the
# curve smooth. To model a specific installed unit, replace these with that
# unit's published NEEP COPs.
#   5 F  -> ~1.75  (NEEP ccASHP minimum at max-capacity operation)
#   17 F -> ~2.1
#   35 F -> ~2.8   (interpolated)
#   47 F -> ~3.6   (typical rated COP at 47 F)
MA_COP_CURVE = COPCurve(
    points=(
        (5.0, 1.75),
        (17.0, 2.1),
        (35.0, 2.8),
        (47.0, 3.6),
    )
)


# ---------------------------------------------------------------------------
# Massachusetts Seasonal_Heat_Pump_Rate (Req 3.2, 6.6)
# ---------------------------------------------------------------------------
#
# The seasonal electric delivery discount applies November 1 through April 30
# inclusive. ``price_key`` is the standard electricity rate; during the window
# the discounted ``seasonal_price_key`` is used instead.
MA_SEASONAL_RATE = SeasonalRateRule(
    price_key="electricity_per_kwh",
    seasonal_price_key="electricity_seasonal_per_kwh",
    season_start=(11, 1),   # Nov 1
    season_end=(4, 30),     # Apr 30
)


# ---------------------------------------------------------------------------
# Massachusetts region profile (Req 3.1, 3.2, 3.6)
# ---------------------------------------------------------------------------
MASSACHUSETTS = RegionProfile(
    region_key=MASSACHUSETTS_KEY,
    fuels=MA_FUELS,
    cop_curve=MA_COP_CURVE,
    seasonal_rate=MA_SEASONAL_RATE,
    base_temperature_f=65.0,   # conventional HDD base, informational
)


# ---------------------------------------------------------------------------
# Region registry and resolution (Req 3.1, 3.3, 3.4, 3.5)
# ---------------------------------------------------------------------------

#: Maps a region key to its :class:`RegionProfile`. Massachusetts is the only
#: built-in region today; additional regions drop in as new entries here.
REGION_REGISTRY: Mapping[str, RegionProfile] = {
    MASSACHUSETTS_KEY: MASSACHUSETTS,
}


def resolve_region(region_key: Optional[str]) -> RegionProfile:
    """Resolve a region key to its :class:`RegionProfile`.

    Looks the key up in :data:`REGION_REGISTRY` and returns the matching
    profile (Req 3.1, 3.3).

    Args:
        region_key: The region identifier, e.g. ``"MA"``.

    Returns:
        The :class:`RegionProfile` registered for ``region_key``.

    Raises:
        MissingRegionKeyError: If ``region_key`` is ``None`` or empty/blank
            (Req 3.5).
        UnknownRegionError: If no profile is registered for ``region_key``.
            The error names the key and no previously loaded profile is mutated
            (Req 3.4).
    """
    # Empty or null key -> missing (Req 3.5). Treat whitespace-only as empty.
    if region_key is None or (isinstance(region_key, str) and region_key.strip() == ""):
        raise MissingRegionKeyError()

    profile = REGION_REGISTRY.get(region_key)
    if profile is None:
        # Unknown key names the offending key; the registry is left unchanged
        # (Req 3.4).
        raise UnknownRegionError(region_key)

    return profile
