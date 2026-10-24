"""Fuel-neutral heating demand model (pure core, Req 4).

This module implements the :func:`compute_demand` function, which converts
per-interval outdoor temperatures and a house's thermal characteristics into a
single fuel-neutral heating-demand number (kWh-thermal) before any fuel
conversion happens (Req 4.5).

The module is a **pure function** of its inputs: it imports only the core data
models and performs no provider access and no I/O, which is part of the
structural guarantee behind Requirements 2.4 and 2.5.

Core math (design "Core Math" section 1):

    degree_diff_i = max(0, S - T_i)                    # zero when T_i >= S
    demand_i      = UA_effective * degree_diff_i * INTERVAL_HOURS
    UA_effective  = ua_coefficient * heated_area_sqft * demand_scale
    total_demand  = sum(demand_i over intervals that have data)

Intervals whose ``outdoor_temp_f`` is ``None`` are excluded from the sum and
collected into ``missing_intervals`` (Req 4.6).
"""

from __future__ import annotations

from typing import List

from energy_cost_predictor.models import (
    DemandResult,
    HouseProfile,
    ModelParameters,
    WeatherSeries,
)

__all__ = ["INTERVAL_HOURS", "compute_demand"]


#: Hours represented by a single interval. The weather series uses daily
#: intervals, so each interval spans a full 24-hour day. Keeping this as a
#: single module constant ensures the per-interval energy conversion stays
#: consistent across the whole model.
INTERVAL_HOURS: float = 24.0


def compute_demand(
    weather: WeatherSeries,
    house: HouseProfile,
    params: ModelParameters,
) -> DemandResult:
    """Compute fuel-neutral heating demand for a weather series (Req 4.1-4.6).

    For each interval with a present outdoor temperature ``T`` and the house
    thermostat setpoint ``S``:

    * ``degree_diff = max(0, S - T)`` — exactly zero when ``T >= S`` (Req 4.3),
    * ``demand = UA_effective * degree_diff * INTERVAL_HOURS`` (kWh-thermal),

    where ``UA_effective = ua_coefficient * heated_area_sqft * demand_scale``
    (Req 4.2). The total is the sum over intervals that have data (Req 4.5).
    Because each term is non-negative and the coefficients are positive,
    lowering any below-setpoint interval's temperature strictly increases that
    interval's term, so a uniformly colder period yields strictly greater total
    demand (Req 4.4).

    Intervals whose ``outdoor_temp_f`` is ``None`` are excluded from the
    computation and reported in ``missing_intervals`` (Req 4.6). The returned
    ``per_interval_kwh_thermal`` corresponds, in order, to the present
    (non-missing) intervals only.

    :param weather: Per-interval outdoor temperatures for the period.
    :param house: The house whose setpoint and thermal characteristics drive
        demand.
    :param params: Tunable model parameters; ``demand_scale`` scales
        ``UA_effective``.
    :returns: A :class:`DemandResult` with the total fuel-neutral demand, the
        present-interval demands, and the list of missing-interval dates.
    """
    setpoint = house.setpoint_f
    ua_effective = house.ua_coefficient * house.heated_area_sqft * params.demand_scale

    per_interval: List[float] = []
    missing: List = []
    total = 0.0

    for interval in weather.intervals:
        temp = interval.outdoor_temp_f
        if temp is None:
            # Missing data: exclude from the sum, record the interval date, and
            # keep processing the remaining intervals (Req 4.6).
            missing.append(interval.interval_date)
            continue

        degree_diff = setpoint - temp
        if degree_diff < 0.0:
            # At or above setpoint contributes exactly zero (Req 4.3).
            degree_diff = 0.0

        demand = ua_effective * degree_diff * INTERVAL_HOURS
        per_interval.append(demand)
        total += demand

    return DemandResult(
        total_demand_kwh_thermal=total,
        per_interval_kwh_thermal=per_interval,
        missing_intervals=missing,
    )
