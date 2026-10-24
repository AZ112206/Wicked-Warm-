"""Runnable example assembling the full Energy Cost Predictor pipeline (Req 1, 7).

This module is the end-to-end wiring described by the design: it constructs the
placeholder providers (the only home of made-up Massachusetts data), builds a
:class:`~energy_cost_predictor.predictor.Predictor` against the default region
registry, and drives one annual run through every stage of the pipeline:

1. :meth:`Predictor.predict_annual_cost` — the estimated annual heating cost and
   the weather intervals that were missing (Req 1.1, 1.3).
2. :meth:`Predictor.compare_savings` — conventional vs heat-pump cost and the
   savings between them (Req 7.1).
3. :class:`~energy_cost_predictor.calibration.Calibration` — tunes
   ``demand_scale`` against the reference actual until within the 1% target.
4. :func:`~energy_cost_predictor.accuracy.compute_accuracy` — the post-calibration
   accuracy report for the now-calibrated predictor.

Everything here is wired and runnable with ``py -m energy_cost_predictor.example``.
There is no orphaned code: every object built is fed into the next stage, and
the predictor the accuracy step reports on is the same instance the calibration
step tuned.

The module depends only on the standard library and the package's own modules —
no ``numpy`` / ``pandas`` and no direct I/O.
"""

from __future__ import annotations

from datetime import date

from energy_cost_predictor.accuracy import compute_accuracy
from energy_cost_predictor.calibration import Calibration
from energy_cost_predictor.models import HouseProfile
from energy_cost_predictor.placeholder_providers import (
    PlaceholderPricingProvider,
    PlaceholderReferenceActualProvider,
    PlaceholderWeatherProvider,
)
from energy_cost_predictor.predictor import Period, Predictor

__all__ = ["main"]


def _build_reference_house() -> HouseProfile:
    """Build the sample Massachusetts reference house used throughout the demo.

    The ``ua_coefficient`` and ``demand_scale`` combine to set the baseline
    annual demand; calibration tunes ``demand_scale`` the rest of the way to the
    reference actual, so the exact starting ``ua_coefficient`` only needs to put
    the baseline cost in a plausible range. ``system_efficiency`` of 0.9 is a
    typical oil/gas furnace efficiency (Req 5.1).
    """
    return HouseProfile(
        house_id="ma-reference-001",
        region_key="MA",
        heated_area_sqft=2000.0,
        ua_coefficient=0.00004,       # loss seed; calibration tunes demand_scale
        setpoint_f=68.0,
        conventional_fuel="heating_oil",
        system_efficiency=0.9,
    )


def main() -> None:
    """Assemble and run the full pipeline, printing each stage's result.

    Builds the placeholder providers, wires a :class:`Predictor` to the default
    MA region registry, and then runs prediction, savings, calibration, and the
    post-calibration accuracy report in sequence. All output is printed to
    stdout in a clearly labeled, human-readable form.
    """
    # 1. Construct the placeholder providers (the only source of made-up data).
    weather_provider = PlaceholderWeatherProvider()
    pricing_provider = PlaceholderPricingProvider()
    reference_provider = PlaceholderReferenceActualProvider()

    # 2. Wire the predictor: default region resolver (resolve_region -> MA) plus
    #    the placeholder weather and pricing providers.
    predictor = Predictor(
        weather_provider=weather_provider,
        pricing_provider=pricing_provider,
    )

    # 3. Sample MA reference house and a full annual period.
    house = _build_reference_house()
    period = Period(start=date(2023, 1, 1), end=date(2023, 12, 31))

    print("=" * 64)
    print("Energy Cost Predictor - full pipeline example (Massachusetts)")
    print("=" * 64)
    print(f"House   : {house.house_id} ({house.heated_area_sqft:.0f} sqft, "
          f"{house.conventional_fuel}, setpoint {house.setpoint_f:.0f}F)")
    print(f"Period  : {period.start.isoformat()} to {period.end.isoformat()}")
    print()

    # 4. Annual cost prediction (Req 1.1, 1.3).
    estimate = predictor.predict_annual_cost(house, period)
    print("-- Annual cost prediction ----------------------------------")
    print(f"Estimated annual heating cost : ${estimate.cost_usd:,.2f}")
    print(f"Missing weather intervals     : {len(estimate.missing_intervals)}")
    print()

    # 5. Savings comparison: conventional vs heat pump (Req 7.1).
    savings = predictor.compare_savings(house, period)
    print("-- Conventional vs heat-pump savings -----------------------")
    print(f"Conventional cost : ${savings.conventional_cost:,.2f}")
    print(f"Heat-pump cost    : ${savings.heat_pump_cost:,.2f}")
    print(f"Savings           : ${savings.savings:,.2f}")
    print()

    # 6. Calibration against the reference actual (tunes predictor.params).
    calibration = Calibration(
        predictor=predictor,
        house=house,
        period=period,
        reference_provider=reference_provider,
    )
    result = calibration.calibrate()
    print("-- Calibration ---------------------------------------------")
    print(f"Best relative error : {result.best_relative_error_pct:.2f}%")
    print(f"Target met          : {result.target_met}")
    print(f"Iterations          : {result.iterations}")
    print(f"Calibrated scale    : {result.params.demand_scale:.6f}")
    print()

    # 7. Post-calibration accuracy report. The predictor now carries the
    #    calibrated params, so re-predicting gives the tuned cost; compare it to
    #    the same reference actual the calibration used.
    predicted_after = predictor.predict_annual_cost(house, period).cost_usd
    actual = reference_provider.get_reference_actual(house.house_id).actual_annual_cost_usd
    report = compute_accuracy(predicted_after, actual)
    print("-- Post-calibration accuracy report ------------------------")
    print(f"Predicted (calibrated) : ${report.predicted:,.2f}")
    print(f"Actual (reference)     : ${report.actual:,.2f}")
    print(f"Relative error         : {report.relative_error_pct:.2f}%")
    print(f"Target met             : {report.target_met}")
    print("=" * 64)


if __name__ == "__main__":
    main()
