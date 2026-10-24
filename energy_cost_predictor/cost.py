"""Cost model for the Energy Cost Predictor (pure core, Req 5, 6).

This module converts a fuel-neutral heating demand (kWh-thermal) into a
monetary heating cost. It currently implements :func:`conventional_cost`, which
prices heating with a conventional fuel system (natural gas, heating oil,
propane, or electric resistance). The heat-pump cost path is implemented
separately and appended to this module.

The functions here are **pure functions** of their inputs: they import only the
core data models and the typed error hierarchy and perform no provider access
and no I/O, which is part of the structural guarantee behind Requirements 2.4
and 2.5.

Core math (design "Core Math" section 2):

    delivered_heat    = total_demand_kwh_thermal
    fuel_energy_input = delivered_heat / (system_efficiency * efficiency_trim)
    fuel_quantity     = fuel_energy_input / fuel.energy_content
    conventional_cost = round(fuel_quantity * price[fuel.price_key], 2)

Guards are checked *before* any arithmetic so a failed cost returns no value and
leaves previously computed values untouched (Req 5.4, 5.5, 5.6).
"""

from __future__ import annotations

from energy_cost_predictor.errors import (
    EfficiencyOutOfRangeError,
    PriceUnavailableError,
    UnsupportedFuelError,
)
from energy_cost_predictor.models import (
    CostResult,
    DemandResult,
    HeatPumpUsage,
    HouseProfile,
    ModelParameters,
    PriceTable,
    RegionProfile,
)
from energy_cost_predictor.seasonal_rate import rate_for_date

__all__ = ["conventional_cost", "heat_pump_cost"]


def conventional_cost(
    demand: DemandResult,
    house: HouseProfile,
    prices: PriceTable,
    region: RegionProfile,
    params: ModelParameters,
) -> CostResult:
    """Compute the conventional heating cost for a house (Req 1.2, 1.4, 5.1-5.6).

    Converts the fuel-neutral demand into a fuel quantity using the fuel's
    energy content and the house's system efficiency, then multiplies by the
    region's unit price for that fuel:

        fuel_energy_input = demand / (system_efficiency * efficiency_trim)
        fuel_quantity     = fuel_energy_input / fuel.energy_content
        cost              = round(fuel_quantity * price, 2)

    The supported fuels and their price units come from the region's
    :class:`~energy_cost_predictor.models.FuelDef` entries: natural gas per
    therm, heating oil per gallon, propane per gallon, and electric resistance
    per kWh (Req 5.3). ``efficiency_trim`` is a calibration multiplier applied
    alongside the system efficiency and is not independently range-checked.

    All guards are evaluated before any arithmetic so a failed cost returns no
    value and leaves previously computed values unchanged (Req 5.4-5.6).

    :param demand: Fuel-neutral heating demand (kWh-thermal) for the period.
    :param house: The house whose ``conventional_fuel`` and ``system_efficiency``
        drive the conversion.
    :param prices: Unit prices keyed by fuel price key for the region.
    :param region: The region profile defining which fuels are available and
        their per-unit energy content (Req 3.6).
    :param params: Tunable model parameters; ``efficiency_trim`` multiplies the
        system efficiency.
    :returns: A :class:`CostResult` with a non-negative cost rounded to 2 dp.
    :raises UnsupportedFuelError: If ``house.conventional_fuel`` is not defined
        for ``region`` (Req 5.4).
    :raises EfficiencyOutOfRangeError: If ``house.system_efficiency`` is <= 0 or
        > 1 (Req 5.5).
    :raises PriceUnavailableError: If the fuel's unit price is missing, ``None``,
        or negative (Req 5.6).
    """
    # Guard 1: the fuel must be defined for the region (Req 5.4). Checked first
    # so an unsupported fuel is reported before any other validation.
    fuel = region.fuels.get(house.conventional_fuel)
    if fuel is None:
        raise UnsupportedFuelError(house.conventional_fuel, region.region_key)

    # Guard 2: system efficiency must lie in (0, 1] (Req 5.1, 5.5). The
    # efficiency_trim calibration multiplier is not independently range-checked.
    efficiency = house.system_efficiency
    if efficiency <= 0.0 or efficiency > 1.0:
        raise EfficiencyOutOfRangeError(efficiency)

    # Guard 3: the fuel unit price must be present, non-null, and non-negative
    # (Req 5.6). A failed lookup raises before any computation, so prior values
    # are retained.
    price = prices.prices.get(fuel.price_key)
    if price is None or price < 0.0:
        raise PriceUnavailableError(fuel.price_key, price)

    # All guards passed; perform the conversion (design "Core Math" section 2).
    delivered_heat = demand.total_demand_kwh_thermal
    fuel_energy_input = delivered_heat / (efficiency * params.efficiency_trim)
    fuel_quantity = fuel_energy_input / fuel.energy_content
    cost = round(fuel_quantity * price, 2)

    return CostResult(cost_usd=cost)


def heat_pump_cost(
    usage: HeatPumpUsage,
    prices: PriceTable,
    region: RegionProfile,
) -> CostResult:
    """Compute the heat-pump heating cost for a period (Req 6.5-6.8).

    Prices each interval's heat-pump electricity at the unit rate that applies
    to that interval's date, then sums and rounds to two decimal places. The
    per-interval rate is selected by
    :func:`~energy_cost_predictor.seasonal_rate.rate_for_date`, which applies the
    Massachusetts seasonal (discounted) delivery rate for dates in the November 1
    through April 30 window and the standard electricity rate otherwise
    (Req 6.6, 6.7). Implements design "Core Math" section 4:

        for each interval (date d, elec_i) in usage.per_interval_kwh:
            rate_d = rate_for_date(region, prices, d)
            cost  += elec_i * rate_d
        cost = round(cost, 2)

    This is a **pure function** of its inputs: it performs no provider access and
    no I/O. A missing or negative electricity price halts the computation by
    raising, so no partial or defaulted value is produced and previously computed
    values are left intact (Req 6.8).

    :param usage: Per-interval and total heat-pump electricity use; only
        ``per_interval_kwh`` (a sequence of ``(date, kwh)`` tuples) is priced.
    :param prices: Unit prices keyed by price key for the region, including the
        standard and (optional) seasonal electricity entries.
    :param region: The region profile supplying the optional seasonal rate rule
        and the electricity price keys consulted per interval.
    :returns: A :class:`CostResult` with a non-negative cost rounded to 2 dp.
    :raises PriceUnavailableError: If the electricity price required for any
        interval is missing, ``None``, or negative (Req 6.8). The error halts the
        sum, so prior values are retained.
    """
    cost = 0.0
    for interval_date, elec_i in usage.per_interval_kwh:
        rate_d = rate_for_date(region, prices, interval_date)
        cost += elec_i * rate_d

    return CostResult(cost_usd=round(cost, 2))
