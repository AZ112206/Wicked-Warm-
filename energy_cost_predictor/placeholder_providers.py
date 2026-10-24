"""Placeholder data providers for the Energy Cost Predictor.

These concrete classes implement the provider ``Protocol`` shapes defined in
:mod:`energy_cost_predictor.providers` with realistic *made-up* Massachusetts
data so the full pipeline runs end-to-end today. Swapping in a real data source
means writing new classes with the same methods — the core algorithm never
changes (Req 2.4, 2.5).

By design this module is the ONLY location that holds the made-up constants:

- Electricity standard rate ≈ $0.17/kWh and a lower seasonal heat-pump delivery
  rate ≈ $0.14/kWh applied Nov 1–Apr 30 (Req 6.6).
- Heating oil ≈ $6.00/gal, natural gas ≈ $2.50/therm, propane ≈ $3.00/gal.
- A synthetic MA daily winter temperature series that is colder in mid-winter
  (December–February) and includes a few ``None`` intervals to exercise the
  missing-data path (Req 4.6, 2.6).
- A plausible positive reference actual annual heating cost.

The price-table keys line up exactly with the Massachusetts ``FuelDef.price_key``
values and the ``SeasonalRateRule`` keys declared in
:mod:`energy_cost_predictor.regions`. The module depends only on the standard
library and :mod:`energy_cost_predictor.models` — no ``numpy``/``pandas`` and no
I/O.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from .models import (
    PriceTable,
    ReferenceActual,
    WeatherInterval,
    WeatherSeries,
)

__all__ = [
    "PlaceholderWeatherProvider",
    "PlaceholderPricingProvider",
    "PlaceholderReferenceActualProvider",
]


# ---------------------------------------------------------------------------
# Made-up constants (live ONLY here)
# ---------------------------------------------------------------------------

# Synthetic MA winter temperature shape, in degrees Fahrenheit. The year's
# coldest point sits in late January; the series swings warmer toward spring and
# fall. These two numbers plus a cosine shape generate a plausible daily curve
# without baking per-day values anywhere.
_ANNUAL_MEAN_TEMP_F = 42.0          # yearly average outdoor temperature
_SEASONAL_AMPLITUDE_F = 27.0        # half the peak-to-trough yearly swing
_COLDEST_DAY_OF_YEAR = 21           # ~Jan 21, the mid-winter minimum

# Every Nth daily interval is reported as missing (None) to exercise the
# missing-data handling in the demand model. A prime stride keeps the gaps from
# lining up on week boundaries.
_MISSING_INTERVAL_STRIDE = 17

# Massachusetts energy unit prices (USD). Keys must match MA_FUELS price keys
# and MA_SEASONAL_RATE in regions.py exactly.
_MA_PRICES = {
    "electricity_per_kwh": 0.17,            # standard electric rate
    "electricity_seasonal_per_kwh": 0.14,   # seasonal heat-pump discount (Req 6.6)
    "natural_gas_per_therm": 2.50,
    "heating_oil_per_gallon": 6.00,
    "propane_per_gallon": 3.00,
}

# A plausible positive known annual heating cost for the built-in reference
# house, used by calibration/accuracy (Req 2.3, 8.6, 9).
_DEFAULT_REFERENCE_ACTUAL_USD = 2450.00


def _synthetic_temp_f(day: date) -> float:
    """Return the synthetic outdoor temperature (°F) for ``day``.

    The curve is a cosine with its minimum at :data:`_COLDEST_DAY_OF_YEAR`,
    producing colder mid-winter temperatures and warmer temperatures toward
    summer. Leap years are handled by using the day's own year length.
    """
    year_start = date(day.year, 1, 1)
    days_in_year = (date(day.year + 1, 1, 1) - year_start).days
    day_of_year = (day - year_start).days

    # Cosine phased so the minimum lands on the coldest day of the year.
    phase = 2.0 * math.pi * (day_of_year - _COLDEST_DAY_OF_YEAR) / days_in_year
    return _ANNUAL_MEAN_TEMP_F - _SEASONAL_AMPLITUDE_F * math.cos(phase)


class PlaceholderWeatherProvider:
    """Synthetic Massachusetts daily winter temperature series (Req 2.1, 2.4).

    Produces one :class:`WeatherInterval` per calendar day in ``[start, end]``
    inclusive. Mid-winter days are colder than shoulder-season days, and a few
    intervals carry ``outdoor_temp_f=None`` so downstream code exercises the
    missing-data path (Req 4.6, 2.6).
    """

    def get_temperatures(
        self, region_key: str, start: date, end: date
    ) -> WeatherSeries:
        """Return daily outdoor temperatures for ``[start, end]`` inclusive.

        Intervals whose zero-based index is a multiple of
        :data:`_MISSING_INTERVAL_STRIDE` are reported as missing (``None``);
        all others carry a synthetic temperature. The returned series preserves
        every day in the range so no interval is silently dropped.
        """
        intervals: list[WeatherInterval] = []
        index = 0
        current = start
        while current <= end:
            if index % _MISSING_INTERVAL_STRIDE == 0 and index != 0:
                temp = None
            else:
                temp = round(_synthetic_temp_f(current), 1)
            intervals.append(
                WeatherInterval(interval_date=current, outdoor_temp_f=temp)
            )
            current = current + timedelta(days=1)
            index += 1

        return WeatherSeries(region_key=region_key, intervals=intervals)


class PlaceholderPricingProvider:
    """Made-up Massachusetts energy unit prices (Req 2.2, 2.4, 6.6).

    Returns a :class:`PriceTable` whose keys match the Massachusetts fuel price
    keys (``electricity_per_kwh``, ``natural_gas_per_therm``,
    ``heating_oil_per_gallon``, ``propane_per_gallon``) and the seasonal
    heat-pump delivery key (``electricity_seasonal_per_kwh``) exactly, so a
    region's :class:`FuelDef` and :class:`SeasonalRateRule` resolve cleanly.
    """

    def get_prices(self, region_key: str) -> PriceTable:
        """Return the unit price table for ``region_key``.

        A fresh ``dict`` copy is returned each call so callers cannot mutate the
        shared constant table.
        """
        return PriceTable(region_key=region_key, prices=dict(_MA_PRICES))


class PlaceholderReferenceActualProvider:
    """Made-up known actual annual heating cost for a reference house.

    Supplies the positive actual annual cost consumed by calibration and
    accuracy reporting (Req 2.3, 8.6). Never hard-coded in the core algorithm —
    it lives here, behind the provider seam.
    """

    def __init__(
        self, actual_annual_cost_usd: float = _DEFAULT_REFERENCE_ACTUAL_USD
    ) -> None:
        self._actual_annual_cost_usd = actual_annual_cost_usd

    def get_reference_actual(self, house_id: str) -> ReferenceActual:
        """Return the known actual annual heating cost for ``house_id``.

        The value is always positive so it is valid for calibration (Req 8.2).
        """
        return ReferenceActual(
            house_id=house_id,
            actual_annual_cost_usd=self._actual_annual_cost_usd,
        )
