"""Core data models for the Energy Cost Predictor.

All models are ``@dataclass(frozen=True)`` to keep the core side-effect-free and
easy to test. These are pure data carriers plus one small piece of behavior
(:meth:`COPCurve.cop_at`); they import no providers and perform no I/O, which is
part of the structural guarantee behind Requirements 2.4 and 2.5.

The module depends only on the Python standard library (``dataclasses``,
``typing``, ``datetime``) — no third-party numeric libraries — so the data layer
is trivially importable in any environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional, Sequence, Tuple

__all__ = [
    "HouseProfile",
    "FuelDef",
    "SeasonalRateRule",
    "RegionProfile",
    "WeatherInterval",
    "WeatherSeries",
    "PriceTable",
    "ReferenceActual",
    "COPCurve",
    "ModelParameters",
    "DemandResult",
    "HeatPumpUsage",
    "CostResult",
    "SavingsResult",
    "AccuracyReport",
    "CalibrationResult",
]


# ---------------------------------------------------------------------------
# Input: house profile (Req 1, 3, 5)
# ---------------------------------------------------------------------------

#: Fields that must be present for a HouseProfile to describe a valid request.
#: A missing one triggers a named-field error at the orchestration boundary
#: (Req 1.5, 7.5). ``house_id`` and ``ua_coefficient`` are internal/seed values
#: and are not part of this required set.
REQUIRED_HOUSE_FIELDS: Tuple[str, ...] = (
    "region_key",
    "heated_area_sqft",
    "setpoint_f",
    "conventional_fuel",
    "system_efficiency",
)


@dataclass(frozen=True)
class HouseProfile:
    """A house to estimate heating cost for (input)."""

    house_id: str
    region_key: str                 # resolves to a RegionProfile (Req 3)
    heated_area_sqft: float
    ua_coefficient: float           # thermal loss coefficient; tunable seed
    setpoint_f: float               # thermostat setpoint (Req 4.2)
    conventional_fuel: str          # "natural_gas" | "heating_oil" | "propane" | "electric_resistance"
    system_efficiency: float        # 0 < eff <= 1 (Req 5.1, 5.5)


# ---------------------------------------------------------------------------
# Region configuration (Req 3, 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FuelDef:
    """Definition of a fuel available in a region (Req 3.6)."""

    fuel_type: str
    price_key: str                  # key into PriceTable (Req 3.6)
    energy_content: float           # per-unit energy (kWh-thermal per therm/gal/kWh)
    unit: str                       # "therm" | "gallon" | "kWh"


@dataclass(frozen=True)
class SeasonalRateRule:
    """Seasonal electricity delivery discount window (Req 3.2, 6.6)."""

    price_key: str                  # electricity standard rate key
    seasonal_price_key: str         # discounted delivery key
    season_start: Tuple[int, int]   # (11, 1) -> Nov 1
    season_end: Tuple[int, int]     # (4, 30) -> Apr 30


@dataclass(frozen=True)
class RegionProfile:
    """Region configuration, with Massachusetts as the primary target."""

    region_key: str
    fuels: Mapping[str, FuelDef]                 # available fuels (Req 3.6)
    cop_curve: "COPCurve"
    seasonal_rate: Optional[SeasonalRateRule]    # set for Massachusetts (Req 3.2, 6.6)
    base_temperature_f: float                    # HDD base, informational


# ---------------------------------------------------------------------------
# Weather and pricing data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeatherInterval:
    """Outdoor temperature for a single interval."""

    interval_date: date
    outdoor_temp_f: Optional[float]   # None => missing (Req 4.6, 2.6)


@dataclass(frozen=True)
class WeatherSeries:
    """A sequence of per-interval outdoor temperatures for a region."""

    region_key: str
    intervals: Sequence[WeatherInterval]


@dataclass(frozen=True)
class PriceTable:
    """Unit prices keyed by fuel price key for a region."""

    region_key: str
    # price_key -> $/unit; electricity standard + optional seasonal entry.
    prices: Mapping[str, float]       # negative/missing => error (Req 5.6, 6.8)


@dataclass(frozen=True)
class ReferenceActual:
    """The known actual annual heating cost for a Reference_House (Req 2.3, 8.6).

    Supplied exclusively through a ``ReferenceActualProvider`` — never
    hard-coded in the algorithm — and consumed by calibration, which needs the
    actual annual cost value to compute relative error against the prediction.
    """

    house_id: str
    actual_annual_cost_usd: float     # must be > 0 for calibration (Req 8.2)


@dataclass(frozen=True)
class COPCurve:
    """Coefficient-of-performance curve for a heat pump (Req 6.2, 6.3).

    ``points`` are ``(temp_f, cop)`` pairs sorted by ascending temperature, with
    COP monotonic non-decreasing as temperature rises (a colder outdoor
    temperature never has a higher COP). Each COP is bounded to ``[1.0, 5.0]``.
    """

    points: Sequence[Tuple[float, float]]   # [(temp_f, cop)], 1.0 <= cop <= 5.0

    def cop_at(self, temp_f: float) -> float:
        """Return the COP at ``temp_f`` by linear interpolation over breakpoints.

        The curve is defined by ascending-temperature breakpoints. For a
        temperature between two breakpoints the COP is linearly interpolated;
        below the lowest breakpoint it clamps to the lowest COP and above the
        highest breakpoint it clamps to the highest COP. Because the breakpoints
        are monotonic non-decreasing in COP, the returned value is likewise
        monotonic non-decreasing in ``temp_f`` (Req 6.3).
        """
        points = self.points
        if not points:
            raise ValueError("COPCurve has no breakpoints")

        # Single breakpoint: COP is constant.
        if len(points) == 1:
            return points[0][1]

        # Clamp outside the defined range.
        first_temp, first_cop = points[0]
        if temp_f <= first_temp:
            return first_cop
        last_temp, last_cop = points[-1]
        if temp_f >= last_temp:
            return last_cop

        # Find the bracketing segment and interpolate linearly.
        for i in range(1, len(points)):
            lo_temp, lo_cop = points[i - 1]
            hi_temp, hi_cop = points[i]
            if lo_temp <= temp_f <= hi_temp:
                span = hi_temp - lo_temp
                if span == 0:
                    # Degenerate segment; take the higher COP to preserve
                    # monotonic-non-decreasing behavior.
                    return hi_cop
                frac = (temp_f - lo_temp) / span
                return lo_cop + frac * (hi_cop - lo_cop)

        # Unreachable given the clamps above, but keep a safe fallback.
        return last_cop


# ---------------------------------------------------------------------------
# Tunable parameters and results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelParameters:
    """Tunable calibration knobs (Req 8)."""

    demand_scale: float = 1.0       # primary calibration knob (UA-style scalar)
    efficiency_trim: float = 1.0    # secondary trim, bounded near 1.0


@dataclass(frozen=True)
class DemandResult:
    """Fuel-neutral heating demand result (Req 4.5)."""

    total_demand_kwh_thermal: float             # fuel-neutral (Req 4.5)
    per_interval_kwh_thermal: Sequence[float]
    missing_intervals: Sequence[date]           # Req 4.6


@dataclass(frozen=True)
class HeatPumpUsage:
    """Per-interval and total heat-pump electricity use (Req 6)."""

    total_kwh: float
    per_interval_kwh: Sequence[Tuple[date, float]]


@dataclass(frozen=True)
class CostResult:
    """A computed heating cost (Req 1.4, 5.2)."""

    cost_usd: float                 # non-negative, rounded 2dp (Req 1.4, 5.2)


@dataclass(frozen=True)
class SavingsResult:
    """Conventional vs heat-pump comparison (Req 7)."""

    conventional_cost: float
    heat_pump_cost: float
    savings: float                  # conventional - heat_pump (Req 7.2, 7.3)


@dataclass(frozen=True)
class AccuracyReport:
    """Relative-error accuracy report for a reference house (Req 9)."""

    predicted: float
    actual: float
    relative_error_pct: float       # Req 9.1, 9.3
    target_met: bool                # Req 9.4, 9.5


@dataclass(frozen=True)
class CalibrationResult:
    """Outcome of a calibration run (Req 8)."""

    params: ModelParameters
    best_relative_error_pct: float
    target_met: bool
    iterations: int
