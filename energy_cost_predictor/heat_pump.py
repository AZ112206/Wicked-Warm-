"""Heat-pump electricity model (pure core, Req 6.1-6.4).

This module implements :func:`electricity_kwh`, which converts the fuel-neutral
per-interval heating demand produced by the demand model into per-interval
heat-pump electricity consumption (kWh) using the region's temperature-dependent
coefficient of performance (COP). It implements Core Math section 3 of the
design document.

The module is a **pure function** of its inputs: it imports only the core data
models and performs no provider access and no I/O, which is part of the
structural guarantee behind Requirements 2.4 and 2.5. It uses only the Python
standard library.

Core math (design "Core Math" section 3):

    demand_i_kwh_thermal  (from the demand step, per present interval)
    cop_i   = cop_curve.cop_at(T_i)          # 1.0 <= cop_i <= 5.0 (Req 6.2, 6.3)
    elec_i  = demand_i_kwh_thermal / cop_i    # skip division when demand_i == 0 (Req 6.4)
    total_kwh = sum(elec_i)

Because ``elec_i`` decreases as ``cop_i`` increases for a fixed thermal demand,
total kWh is monotonic (non-increasing) in COP, and because a lower temperature
is assigned a lower-or-equal COP, a colder interval consumes greater-or-equal
electricity for the same thermal demand (Req 6.3).

Alignment note
--------------
:class:`~energy_cost_predictor.models.DemandResult.per_interval_kwh_thermal`
corresponds, in order, to the **present** (non-missing) weather intervals only;
missing intervals are listed in ``demand.missing_intervals``. To pair each
per-interval demand with the correct temperature and date for the COP lookup,
this function iterates the weather intervals skipping any whose
``outdoor_temp_f`` is ``None`` (equivalently, whose date is a missing interval),
so ``demand[i]`` lines up with the i-th present interval's temperature and date.
"""

from __future__ import annotations

from datetime import date
from typing import List, Tuple

from energy_cost_predictor.models import (
    COPCurve,
    DemandResult,
    HeatPumpUsage,
    WeatherSeries,
)

__all__ = ["electricity_kwh"]


def electricity_kwh(
    weather: WeatherSeries,
    demand: DemandResult,
    cop_curve: COPCurve,
) -> HeatPumpUsage:
    """Compute per-interval and total heat-pump electricity use (Req 6.1-6.4).

    For each present (non-missing) interval with outdoor temperature ``T`` and
    fuel-neutral thermal demand ``demand_i`` (kWh-thermal):

    * ``cop_i = cop_curve.cop_at(T)`` — bounded to ``[1.0, 5.0]`` and monotonic
      non-decreasing in temperature (Req 6.2, 6.3),
    * ``elec_i = demand_i / cop_i`` (kWh), **except** when ``demand_i == 0`` the
      result is exactly ``0.0`` and no division is performed (Req 6.4).

    The per-interval electricity is paired with that interval's date, and the
    total is the sum across all present intervals.

    The present intervals are selected by iterating ``weather.intervals`` and
    skipping any whose ``outdoor_temp_f`` is ``None`` (these are the intervals
    the demand model excluded and reported in ``demand.missing_intervals``), so
    ``demand.per_interval_kwh_thermal[i]`` aligns with the i-th present
    interval's temperature and date.

    :param weather: Per-interval outdoor temperatures for the period. Intervals
        with a ``None`` temperature are skipped so the demands line up with the
        present intervals.
    :param demand: The fuel-neutral demand result whose
        ``per_interval_kwh_thermal`` is indexed in present-interval order.
    :param cop_curve: The region's COP curve; ``cop_at`` supplies the COP for
        each present interval's temperature.
    :returns: A :class:`HeatPumpUsage` with per-interval ``(date, kwh)`` pairs
        in present-interval order and the summed total.
    :raises ValueError: If the number of present weather intervals does not match
        the number of per-interval demands, which would indicate the demand was
        computed from a different weather series.
    """
    demands = demand.per_interval_kwh_thermal

    per_interval: List[Tuple[date, float]] = []
    total_kwh = 0.0

    index = 0
    n_demands = len(demands)

    for interval in weather.intervals:
        if interval.outdoor_temp_f is None:
            # Missing interval: excluded by the demand model, so it has no
            # corresponding per-interval demand. Skip it (Req 4.6 alignment).
            continue

        if index >= n_demands:
            raise ValueError(
                "weather has more present intervals than demand entries; "
                "the demand result does not match this weather series"
            )

        demand_i = demands[index]
        index += 1

        if demand_i == 0:
            # Zero thermal demand consumes zero electricity, and we must NOT
            # perform a division (Req 6.4).
            elec_i = 0.0
        else:
            cop_i = cop_curve.cop_at(interval.outdoor_temp_f)
            elec_i = demand_i / cop_i

        per_interval.append((interval.interval_date, elec_i))
        total_kwh += elec_i

    if index != n_demands:
        raise ValueError(
            "demand has more per-interval entries than weather present "
            "intervals; the demand result does not match this weather series"
        )

    return HeatPumpUsage(total_kwh=total_kwh, per_interval_kwh=per_interval)
