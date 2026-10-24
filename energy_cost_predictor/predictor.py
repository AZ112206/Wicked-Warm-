"""Predictor orchestrator for the Energy Cost Predictor (Req 1, 2, 3).

This module wires the pure core together with the pluggable data providers. The
:class:`Predictor` is the thin orchestration layer from the design: it validates
the request (Req 1.5, 1.7), resolves the region (Req 3.1-3.5), fetches weather
and prices through the timeout- and field-guarded provider adapters (Req 2.1,
2.2, 2.6, 2.7), runs the fuel-neutral demand model and then the conventional
cost model, and returns an :class:`EstimateResult` whose cost is non-negative
and rounded to two decimal places (Req 1.4).

Data flows one direction only: providers -> core -> result. The core models
(:func:`~energy_cost_predictor.demand.compute_demand`,
:func:`~energy_cost_predictor.cost.conventional_cost`) never call back out to
I/O; the adapters are the single timeout-aware seam (Req 2.5).

The ``Period`` and ``EstimateResult`` dataclasses are defined here (rather than
in ``models.py``) so the pure-data model layer stays free of orchestration
concerns. ``Period`` carries the request's ``start``/``end`` dates; the design's
request sequence references it as the second argument to ``predict_annual_cost``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional, Sequence, Union

from energy_cost_predictor.cost import conventional_cost, heat_pump_cost
from energy_cost_predictor.demand import compute_demand
from energy_cost_predictor.errors import (
    DependencyUnavailableError,
    InvalidPeriodError,
    MissingFieldError,
)
from energy_cost_predictor.heat_pump import electricity_kwh
from energy_cost_predictor.models import (
    HouseProfile,
    ModelParameters,
    REQUIRED_HOUSE_FIELDS,
    RegionProfile,
    SavingsResult,
)
from energy_cost_predictor.provider_adapter import (
    PricingProviderAdapter,
    WeatherProviderAdapter,
)
from energy_cost_predictor.regions import resolve_region as _default_resolve_region

__all__ = ["Period", "EstimateResult", "SavingsResult", "Predictor"]


#: A region resolver is either the ``resolve_region`` function (or any callable
#: taking a region key and returning a :class:`RegionProfile`) or a mapping of
#: region key -> :class:`RegionProfile`.
RegionResolver = Union[Callable[[Optional[str]], RegionProfile], "object"]


@dataclass(frozen=True)
class Period:
    """A heating period bounded by an inclusive ``start`` and ``end`` date.

    The design's request sequence passes a ``Period`` as the second argument to
    :meth:`Predictor.predict_annual_cost`. A period whose ``end`` falls before
    its ``start`` is invalid and rejected by the predictor (Req 1.7).
    """

    start: date
    end: date


@dataclass(frozen=True)
class EstimateResult:
    """The result of a single annual-cost prediction (Req 1.4, 4.6).

    ``cost_usd`` is the estimated heating cost in United States dollars,
    non-negative and rounded to two decimal places (Req 1.4). ``missing_intervals``
    carries the dates the weather provider reported with no temperature, which
    the demand model excluded from the computation, so callers can see exactly
    which intervals were dropped (Req 4.6).
    """

    cost_usd: float
    missing_intervals: Sequence[date]


class Predictor:
    """Orchestrates a heating-cost prediction from house + period to a result.

    The predictor validates the request, resolves the region, fetches weather
    and prices through guarded adapters, runs the demand and cost models, and
    returns an :class:`EstimateResult`. It holds a tunable
    :class:`~energy_cost_predictor.models.ModelParameters` on ``self.params`` so
    calibration can inject tuned parameters for subsequent same-region
    predictions (Req 8.4).
    """

    def __init__(
        self,
        regions: Optional[RegionResolver] = None,
        weather_provider=None,
        pricing_provider=None,
        demand_model=None,
        cost_model=None,
        heat_pump_model=None,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Wire the predictor to its region resolver, providers, and core models.

        :param regions: A region resolver — either a callable like
            :func:`~energy_cost_predictor.regions.resolve_region` (the default)
            or a mapping of region key to
            :class:`~energy_cost_predictor.models.RegionProfile`.
        :param weather_provider: An object exposing the ``WeatherProvider``
            Protocol shape. It is wrapped in a
            :class:`~energy_cost_predictor.provider_adapter.WeatherProviderAdapter`
            so field and timeout guards apply (Req 2.6, 2.7).
        :param pricing_provider: An object exposing the ``PricingProvider``
            Protocol shape, wrapped in a
            :class:`~energy_cost_predictor.provider_adapter.PricingProviderAdapter`.
            Required for a prediction; absence raises
            :class:`DependencyUnavailableError` at predict time (Req 1.6).
        :param demand_model: The demand model callable. Defaults to the
            module-level :func:`~energy_cost_predictor.demand.compute_demand`.
            Its absence raises :class:`DependencyUnavailableError` (Req 1.6).
        :param cost_model: The conventional cost callable. Defaults to
            :func:`~energy_cost_predictor.cost.conventional_cost`.
        :param heat_pump_model: The heat-pump model, retained for the savings
            comparison path (added later); unused by
            :meth:`predict_annual_cost`.
        :param timeout_seconds: The provider deadline in seconds passed to the
            adapters (Req 2.7).
        """
        # Region resolver: accept a callable or a mapping; default to the
        # built-in registry lookup. A mapping is adapted to the callable shape
        # (Req 3.1-3.5 are enforced by resolve_region for the callable path; a
        # mapping lookup mirrors the "unknown/missing key" behavior below).
        self.regions: RegionResolver = (
            regions if regions is not None else _default_resolve_region
        )

        self.timeout_seconds = timeout_seconds

        # Keep raw providers accessible, and expose guarded adapters for the
        # fetch path. Wrapping is skipped when the provider is None so the
        # dependency guard in predict_annual_cost can report it by name.
        self.weather_provider = weather_provider
        self.pricing_provider = pricing_provider
        self.weather_adapter = (
            WeatherProviderAdapter(weather_provider, timeout_seconds=timeout_seconds)
            if weather_provider is not None
            else None
        )
        self.pricing_adapter = (
            PricingProviderAdapter(pricing_provider, timeout_seconds=timeout_seconds)
            if pricing_provider is not None
            else None
        )

        # Core models default to the module-level pure functions.
        self.demand_model = demand_model if demand_model is not None else compute_demand
        self.cost_model = cost_model if cost_model is not None else conventional_cost
        self.heat_pump_model = heat_pump_model

        # Tunable parameters; calibration (task 10.2) can replace this with a
        # tuned ModelParameters for same-region predictions (Req 8.4).
        self.params: ModelParameters = ModelParameters()

    def _resolve_region(self, region_key: Optional[str]) -> RegionProfile:
        """Resolve a region key via the configured resolver (Req 3.1-3.5)."""
        resolver = self.regions
        if callable(resolver):
            return resolver(region_key)
        # Mapping path: mirror resolve_region's missing/unknown behavior so the
        # same typed errors surface regardless of resolver flavor.
        from energy_cost_predictor.errors import (
            MissingRegionKeyError,
            UnknownRegionError,
        )

        if region_key is None or (
            isinstance(region_key, str) and region_key.strip() == ""
        ):
            raise MissingRegionKeyError()
        profile = resolver.get(region_key)  # type: ignore[union-attr]
        if profile is None:
            raise UnknownRegionError(region_key)
        return profile

    def predict_annual_cost(
        self, house: HouseProfile, period: Period
    ) -> EstimateResult:
        """Predict the heating cost for ``house`` over ``period`` (Req 1.1-1.7).

        The computation is trivially fast (pure arithmetic over daily intervals)
        and completes well within the 5-second budget (Req 1.1). The estimate is
        the fuel-neutral heating demand converted to a fuel cost at the region's
        unit prices (Req 1.2), returned as a non-negative value rounded to two
        decimal places (Req 1.4).

        :param house: The house to estimate. Required fields
            (:data:`~energy_cost_predictor.models.REQUIRED_HOUSE_FIELDS`) must be
            present and non-empty.
        :param period: The inclusive heating period; ``end`` must not precede
            ``start``.
        :returns: An :class:`EstimateResult` with the rounded cost and the list
            of missing weather intervals.
        :raises MissingFieldError: If a required ``HouseProfile`` field is
            missing or empty (Req 1.5).
        :raises InvalidPeriodError: If ``period.end`` precedes ``period.start``
            (Req 1.7).
        :raises DependencyUnavailableError: If the pricing provider or the demand
            model is unavailable (Req 1.6).
        :raises MissingRegionKeyError: If the house's region key is empty/null
            (Req 3.5).
        :raises UnknownRegionError: If the house's region key is unknown
            (Req 3.4).
        """
        # 1. Validate required HouseProfile fields (Req 1.5). A field counts as
        #    missing when it is None or an empty/blank string.
        self._validate_house(house)

        # 2. Validate the period: end must not precede start (Req 1.7).
        if period.end < period.start:
            raise InvalidPeriodError(
                f"period end {period.end.isoformat()} is earlier than start "
                f"{period.start.isoformat()}"
            )

        # 3. Guard dependencies before doing any work (Req 1.6). The pricing
        #    provider and the demand model must both be available; name the one
        #    that is not.
        if self.pricing_adapter is None or self.pricing_provider is None:
            raise DependencyUnavailableError("pricing_provider")
        if self.demand_model is None or not callable(self.demand_model):
            raise DependencyUnavailableError("demand_model")
        if self.weather_adapter is None or self.weather_provider is None:
            raise DependencyUnavailableError("weather_provider")

        # 4. Resolve the region (Req 3.1-3.5).
        region = self._resolve_region(house.region_key)

        # 5. Fetch weather and prices through the guarded adapters (Req 2.1,
        #    2.2, 2.6, 2.7).
        weather = self.weather_adapter.get_temperatures(
            house.region_key, period.start, period.end
        )
        prices = self.pricing_adapter.get_prices(house.region_key)

        # 6. Run the pure core: fuel-neutral demand, then conventional cost.
        demand = self.demand_model(weather, house, self.params)
        cost = self.cost_model(demand, house, prices, region, self.params)

        # 7. Return the rounded, non-negative estimate plus the excluded
        #    (missing) intervals (Req 1.4, 4.6).
        return EstimateResult(
            cost_usd=round(cost.cost_usd, 2),
            missing_intervals=demand.missing_intervals,
        )

    def compare_savings(
        self, house: HouseProfile, period: Period
    ) -> SavingsResult:
        """Compare conventional vs heat-pump annual cost for ``house`` (Req 7).

        Validates the request, confirms the period is an annual period of 12
        consecutive months (Req 7.6), resolves the region, fetches weather and
        prices through the guarded adapters, and runs both cost paths over the
        same fuel-neutral demand (design "Core Math" section 5):

            conventional = conventional_cost(demand, house, prices, region, params)
            usage        = electricity_kwh(weather, demand, region.cop_curve)
            heat_pump    = heat_pump_cost(usage, prices, region)
            savings      = round(conventional - heat_pump, 2)   # may be negative

        :param house: The house to estimate. Required fields
            (:data:`~energy_cost_predictor.models.REQUIRED_HOUSE_FIELDS`) must be
            present and non-empty (Req 7.5).
        :param period: The annual heating period; it must span exactly 12
            consecutive months from ``period.start`` (Req 7.6).
        :returns: A :class:`~energy_cost_predictor.models.SavingsResult` with the
            rounded conventional cost, heat-pump cost, and their difference.
        :raises MissingFieldError: If a required ``HouseProfile`` field is
            missing or empty (Req 7.5).
        :raises InvalidPeriodError: If ``period.end`` precedes ``period.start``
            or the period is not 12 consecutive months (Req 7.6).
        :raises DependencyUnavailableError: If the pricing provider, demand
            model, or weather provider is unavailable (Req 1.6).
        :raises MissingRegionKeyError: If the house's region key is empty/null
            (Req 3.5).
        :raises UnknownRegionError: If the house's region key is unknown
            (Req 3.4).
        """
        # 1. Validate required HouseProfile fields (Req 7.5). Reuses the same
        #    field check as predict_annual_cost.
        self._validate_house(house)

        # 2. Validate the period: end must not precede start, and it must be an
        #    annual period of 12 consecutive months (Req 7.6).
        if period.end < period.start:
            raise InvalidPeriodError(
                f"period end {period.end.isoformat()} is earlier than start "
                f"{period.start.isoformat()}"
            )
        if not self._is_annual_period(period):
            raise InvalidPeriodError(
                f"period {period.start.isoformat()} to {period.end.isoformat()} "
                "is not an annual period of 12 consecutive months"
            )

        # 3. Guard dependencies before doing any work (Req 1.6), mirroring
        #    predict_annual_cost.
        if self.pricing_adapter is None or self.pricing_provider is None:
            raise DependencyUnavailableError("pricing_provider")
        if self.demand_model is None or not callable(self.demand_model):
            raise DependencyUnavailableError("demand_model")
        if self.weather_adapter is None or self.weather_provider is None:
            raise DependencyUnavailableError("weather_provider")

        # 4. Resolve the region (Req 3.1-3.5).
        region = self._resolve_region(house.region_key)

        # 5. Fetch weather and prices through the guarded adapters.
        weather = self.weather_adapter.get_temperatures(
            house.region_key, period.start, period.end
        )
        prices = self.pricing_adapter.get_prices(house.region_key)

        # 6. Compute the shared fuel-neutral demand, then both cost paths.
        demand = self.demand_model(weather, house, self.params)

        conventional = conventional_cost(
            demand, house, prices, region, self.params
        ).cost_usd

        usage = electricity_kwh(weather, demand, region.cop_curve)
        heat_pump = heat_pump_cost(usage, prices, region).cost_usd

        # 7. Savings = conventional - heat_pump, rounded to 2dp (may be negative,
        #    Req 7.2, 7.3). Cost values are expressed within 0.00-999,999.99
        #    (Req 7.4).
        savings = round(conventional - heat_pump, 2)

        return SavingsResult(
            conventional_cost=round(conventional, 2),
            heat_pump_cost=round(heat_pump, 2),
            savings=savings,
        )

    @staticmethod
    def _is_annual_period(period: Period) -> bool:
        """Return True when ``period`` spans exactly 12 consecutive months.

        A period is annual when its inclusive ``[start, end]`` range covers a
        full year: the day after ``end`` equals the same month and day one year
        after ``start`` (Req 7.6). Expressed as ``end + 1 day == start`` advanced
        by exactly one year.

        A Feb 29 start is handled by anchoring the one-year advance on the start
        year: the natural inclusive end of a leap-day year is Feb 28 of the next
        year (the day before the non-leap Mar 1 boundary), which this check
        accepts.
        """
        from datetime import timedelta

        start = period.start
        end = period.end

        # The exclusive end of an annual period is start advanced by one year.
        try:
            next_year_same_day = start.replace(year=start.year + 1)
        except ValueError:
            # start is Feb 29 and start.year + 1 is not a leap year: the one-year
            # boundary falls on Mar 1 of the next year.
            next_year_same_day = date(start.year + 1, 3, 1)

        expected_end = next_year_same_day - timedelta(days=1)
        return end == expected_end

    @staticmethod
    def _validate_house(house: HouseProfile) -> None:
        """Raise ``MissingFieldError`` for the first missing required field.

        A required field (:data:`REQUIRED_HOUSE_FIELDS`) is considered missing
        when its value is ``None`` or an empty/blank string (Req 1.5).
        """
        for field_name in REQUIRED_HOUSE_FIELDS:
            value = getattr(house, field_name, None)
            if value is None:
                raise MissingFieldError(field_name)
            if isinstance(value, str) and value.strip() == "":
                raise MissingFieldError(field_name)
