# Implementation Plan: Energy Cost Predictor

## Overview

This plan implements the Energy Cost Predictor in Python 3.14 using `numpy` 2.5.3 and `pandas` 3.1.0 for interval arithmetic and `hypothesis` for property-based testing. Work proceeds bottom-up so each step builds on the last with no orphaned code: first the frozen dataclasses and typed exceptions, then the provider `Protocol`s and their placeholder implementations, then the pure core models (demand, heat pump, cost, seasonal rate), then the `Predictor` orchestrator, savings comparison, and finally calibration and accuracy reporting wired together through a runnable example. Property-based tests (one per correctness Property 1–10) and error-path unit tests are placed next to the code they validate so regressions surface early.

All code lives under a package `energy_cost_predictor/` with tests under `tests/`. The pure core modules never import a provider or touch I/O, which is the structural guarantee behind Requirements 2.4 and 2.5.

## Tasks

- [x] 1. Set up project structure, dataclasses, and typed exceptions
  - [x] 1.1 Create package layout and tooling
    - Create `energy_cost_predictor/__init__.py`, `tests/__init__.py`, and a `pyproject.toml` (or `requirements.txt`) pinning `numpy==2.5.3`, `pandas==3.1.0`, and `hypothesis`
    - Configure the test runner (pytest) and a `tests/conftest.py` placeholder
    - _Requirements: 2.1_

  - [x] 1.2 Define core data models as frozen dataclasses
    - In `energy_cost_predictor/models.py` implement `HouseProfile`, `FuelDef`, `SeasonalRateRule`, `RegionProfile`, `WeatherInterval`, `WeatherSeries`, `PriceTable`, `COPCurve` (with `cop_at`), `ModelParameters`, `DemandResult`, `HeatPumpUsage`, `CostResult`, `SavingsResult`, `AccuracyReport`, `CalibrationResult`
    - Mark required `HouseProfile` fields and implement `COPCurve.cop_at` as monotonic-non-decreasing interpolation/lookup over ascending breakpoints
    - _Requirements: 1.4, 3.6, 4.5, 6.2, 6.3, 7.1, 7.2_

  - [x] 1.3 Define the typed exception hierarchy
    - In `energy_cost_predictor/errors.py` implement `MissingFieldError`, `InvalidPeriodError`, `DependencyUnavailableError`, `ProviderDataError`, `ProviderTimeoutError`, `UnknownRegionError`, `MissingRegionKeyError`, `UnsupportedFuelError`, `EfficiencyOutOfRangeError`, `PriceUnavailableError`, `InvalidActualCostError`, `AccuracyUncomputableError`
    - Each carries the offending field/provider/region/fuel name per the Error Handling table
    - _Requirements: 1.5, 1.6, 1.7, 2.6, 2.7, 3.4, 3.5, 5.4, 5.5, 5.6, 6.8, 7.5, 7.6, 8.2, 8.7, 9.2_

- [x] 2. Define provider Protocols and placeholder providers
  - [x] 2.1 Define provider Protocol interfaces
    - In `energy_cost_predictor/providers.py` define `WeatherProvider`, `PricingProvider`, `ReferenceActualProvider` as `typing.Protocol` with the exact method shapes from the design (`get_temperatures`, `get_prices`, `get_reference_actual`)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 2.2 Implement placeholder providers with made-up MA data
    - In `energy_cost_predictor/placeholder_providers.py` implement `PlaceholderWeatherProvider` (synthetic MA daily winter series, colder mid-winter, some `None` intervals), `PlaceholderPricingProvider` (electric ≈$0.17/kWh + seasonal discount, oil ≈$6.00/gal, gas ≈$2.50/therm, propane ≈$3.00/gal), and `PlaceholderReferenceActualProvider`
    - These classes are the ONLY location holding made-up constants; return field names match the Protocols
    - _Requirements: 2.4, 6.6_

  - [x] 2.3 Implement the provider-adapter boundary (field + timeout guards)
    - In `energy_cost_predictor/provider_adapter.py` wrap each provider call to raise `ProviderDataError` on absent/null/empty required fields (naming provider + each missing field) and `ProviderTimeoutError` when no response arrives within a 30s deadline (concurrent-futures style wait)
    - Keep the core unaware of I/O; adapters are the only timeout-aware layer
    - _Requirements: 2.6, 2.7, 8.7_

  - [ ]* 2.4 Write unit tests for the provider-adapter error paths
    - Test missing/null/empty required field raises `ProviderDataError` naming provider + fields (2.6)
    - Test a slow provider raises `ProviderTimeoutError` naming the provider (2.7)
    - _Requirements: 2.6, 2.7_

- [x] 3. Implement region configuration and resolution
  - [x] 3.1 Build the Massachusetts RegionProfile and a region registry
    - In `energy_cost_predictor/regions.py` construct the MA `RegionProfile` (fuels with price keys + energy content, falling COP curve ~3.5@47°F→~1.8@5°F, `SeasonalRateRule` Nov 1–Apr 30, base temperature) and a `resolve_region(region_key)` registry lookup
    - Raise `MissingRegionKeyError` on empty/null key and `UnknownRegionError` (naming the key, leaving any prior profile unchanged) on unknown key
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 3.2 Write unit tests for region resolution
    - Test MA key loads the seasonal rule; unknown key raises `UnknownRegionError` naming the key; empty/null key raises `MissingRegionKeyError`
    - _Requirements: 3.2, 3.4, 3.5_

- [x] 4. Implement the HeatingDemandModel (pure core)
  - [x] 4.1 Implement fuel-neutral heating demand computation
    - In `energy_cost_predictor/demand.py` implement `compute_demand(weather, house, params)` returning `DemandResult`: per-interval `degree_diff = max(0, setpoint - T)`, `UA_effective = ua_coefficient * heated_area_sqft * demand_scale`, sum over present intervals; exclude `None` intervals and list them in `missing_intervals`
    - Pure function; no provider or I/O imports
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 4.2 Write property test: zero demand at or above setpoint
    - `# Feature: energy-cost-predictor, Property 1: Zero demand at or above setpoint`
    - **Property 1** — every interval with `T >= setpoint` contributes exactly zero; an all-at/above-setpoint period totals exactly zero. `@settings(max_examples=100)`
    - **Validates: Requirements 4.3**

  - [ ]* 4.3 Write property test: colder periods yield strictly greater demand
    - `# Feature: energy-cost-predictor, Property 2: Colder periods yield strictly greater demand`
    - **Property 2** — a uniformly strictly-colder variant (still at/below setpoint where it was below) yields strictly greater total demand. `@settings(max_examples=100)`
    - **Validates: Requirements 4.4**

  - [ ]* 4.4 Write property test: missing intervals excluded and reported
    - `# Feature: energy-cost-predictor, Property 4: Missing intervals are excluded and reported`
    - **Property 4** — total equals sum over present intervals only; `missing_intervals` equals exactly the set of `None` intervals. `@settings(max_examples=100)`
    - **Validates: Requirements 4.6**

  - [ ]* 4.5 Write unit test for fuel-neutrality of demand
    - Assert demand is independent of `conventional_fuel` for an otherwise identical house
    - _Requirements: 4.5_

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement the HeatPumpModel and SeasonalHeatPumpRate (pure core)
  - [x] 6.1 Implement heat-pump electricity from the COP curve
    - In `energy_cost_predictor/heat_pump.py` implement `electricity_kwh(weather, demand, cop_curve)`: per interval `elec = demand_i / cop_at(T)`, skipping division entirely when `demand_i == 0`; return `HeatPumpUsage` with per-interval `(date, kwh)` and total
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 6.2 Implement the seasonal rate selector
    - In `energy_cost_predictor/seasonal_rate.py` implement a function on the `RegionProfile` returning the seasonal rate iff Nov 1 ≤ date ≤ Apr 30 inclusive (wrapping the year boundary), else the standard rate
    - _Requirements: 6.6, 6.7_

  - [ ]* 6.3 Write property test: heat-pump electricity = demand / COP and monotonic in COP
    - `# Feature: energy-cost-predictor, Property 5: Heat-pump electricity is kWh = demand / COP and monotonic in COP`
    - **Property 5** — for fixed non-zero demand, `elec = demand / cop`; higher COP ⇒ ≤ electricity; COP curve assigns lower-or-equal COP to lower temperatures. `@settings(max_examples=100)`
    - **Validates: Requirements 6.1, 6.3**

  - [ ]* 6.4 Write property test: zero demand produces zero heat-pump electricity
    - `# Feature: energy-cost-predictor, Property 6: Zero demand produces zero heat-pump electricity`
    - **Property 6** — zero interval demand ⇒ exactly zero kWh with no division performed. `@settings(max_examples=100)`
    - **Validates: Requirements 6.4**

  - [ ]* 6.5 Write property test: seasonal rate applies exactly within Nov 1–Apr 30
    - `# Feature: energy-cost-predictor, Property 7: Seasonal rate applies exactly within the Nov 1-Apr 30 window`
    - **Property 7** — applied rate is seasonal iff date ∈ [Nov 1, Apr 30] inclusive, standard otherwise; generators include dates exactly on Nov 1 and Apr 30. `@settings(max_examples=100)`
    - **Validates: Requirements 6.6, 6.7**

- [x] 7. Implement the CostModel (pure core)
  - [x] 7.1 Implement conventional heating cost
    - In `energy_cost_predictor/cost.py` implement `conventional_cost(demand, house, prices, region, params)`: `fuel_input = demand / (efficiency * efficiency_trim)`, `quantity = fuel_input / energy_content`, `cost = round(quantity * price, 2)`; support gas/therm, oil/gal, propane/gal, electric-resistance/kWh
    - Guards: `UnsupportedFuelError` (fuel not in region), `EfficiencyOutOfRangeError` (efficiency ∉ (0,1]), `PriceUnavailableError` (missing/negative price, prior values retained)
    - _Requirements: 1.2, 1.4, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 7.2 Implement heat-pump heating cost with seasonal rating
    - In `energy_cost_predictor/cost.py` implement `heat_pump_cost(usage, prices, region)`: sum per-interval `elec * rate(date)` using the seasonal selector, `round(.., 2)`; raise `PriceUnavailableError` on missing electricity price, leaving prior values intact
    - _Requirements: 6.5, 6.6, 6.7, 6.8_

  - [ ]* 7.3 Write property test: conventional cost conversion correct, non-negative, rounded
    - `# Feature: energy-cost-predictor, Property 3: Conventional cost conversion is correct, non-negative, and rounded`
    - **Property 3** — cost equals `round(demand / efficiency / energy_content * price, 2)`, is non-negative, and equals its own 2dp rounding. `@settings(max_examples=100)`
    - **Validates: Requirements 1.2, 1.4, 5.1, 5.2**

  - [ ]* 7.4 Write unit tests for cost error paths and per-fuel examples
    - Unsupported fuel (5.4), efficiency out of range (5.5), missing/negative price (5.6, 6.8); one representative example per supported fuel pinning units (5.3)
    - _Requirements: 5.3, 5.4, 5.5, 5.6, 6.8_

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement the Predictor orchestrator and SavingsComparison
  - [x] 9.1 Implement the Predictor with input validation and provider wiring
    - In `energy_cost_predictor/predictor.py` implement `Predictor.__init__` and `predict_annual_cost(house, period)`: validate required `HouseProfile` fields (`MissingFieldError`) and period (`InvalidPeriodError` when end < start), resolve region, fetch weather + prices via the adapter, run demand then cost, return an `EstimateResult` with cost rounded to 2dp
    - Raise `DependencyUnavailableError` when the demand model or pricing provider is unavailable
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.1, 2.2_

  - [x] 9.2 Implement SavingsComparison
    - In `energy_cost_predictor/predictor.py` implement `compare_savings(house, period)`: validate a 12-consecutive-month annual period (`InvalidPeriodError` on 7.6, `MissingFieldError`/invalid house on 7.5), compute conventional and heat-pump costs, `savings = round(conventional - heat_pump, 2)` (may be negative), each value within 0.00–999,999.99
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 9.3 Write property test: savings equal conventional minus heat-pump
    - `# Feature: energy-cost-predictor, Property 8: Savings equal conventional minus heat-pump cost`
    - **Property 8** — reported savings equal `round(conventional - heat_pump, 2)`, including negatives when heat-pump cost is greater; generators include that case. `@settings(max_examples=100)`
    - **Validates: Requirements 7.2, 7.3**

  - [ ]* 9.4 Write unit tests for predictor/savings validation paths
    - Missing field (1.5/7.5), reversed period (1.7), non-annual period (7.6), dependency unavailable (1.6)
    - _Requirements: 1.5, 1.6, 1.7, 7.5, 7.6_

- [x] 10. Implement Calibration and AccuracyReport
  - [x] 10.1 Implement AccuracyReport
    - In `energy_cost_predictor/accuracy.py` implement relative-error computation `abs(predicted - actual) / actual`, `relative_error_pct = round(..*100, 2)`, `target_met = pct <= 1.00`; raise `AccuracyUncomputableError` when actual == 0
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 10.2 Implement single-scalar bisection Calibration
    - In `energy_cost_predictor/calibration.py` obtain the reference actual via `ReferenceActualProvider` (adapter, 30s timeout), reject actual ≤ 0 (`InvalidActualCostError`), then bisect `demand_scale` using the monotonic predicted-cost relationship until relative error ≤ 1% or 100 iterations; track best error non-increasing; on failure return best error and retain its params; on success retain calibrated params for same-region predictions
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 10.3 Write property test: relative error and accuracy target computed correctly
    - `# Feature: energy-cost-predictor, Property 9: Relative error and accuracy target are computed correctly`
    - **Property 9** — for actual > 0, pct equals `round(abs(predicted-actual)/actual*100, 2)`, is non-negative, target met iff pct ≤ 1.00. `@settings(max_examples=100)`
    - **Validates: Requirements 9.1, 9.3, 9.4, 9.5**

  - [ ]* 10.4 Write property test: calibrated reference prediction lands within the target
    - `# Feature: energy-cost-predictor, Property 10: Calibrated reference prediction lands within the accuracy target`
    - **Property 10** — for reachable actual > 0 within search bounds, calibration terminates with relative error ≤ 1%. `@settings(max_examples=100)`
    - **Validates: Requirements 8.1, 8.3**

  - [ ]* 10.5 Write unit tests for calibration error/non-convergence paths
    - Actual ≤ 0 raises `InvalidActualCostError` with no adjustment (8.2); unreachable target returns best error and retains those params (8.5); reference-actual timeout aborts (8.7)
    - _Requirements: 8.2, 8.5, 8.7_

- [x] 11. Wire the pipeline together with a runnable example and integration tests
  - [x] 11.1 Add an example entry point assembling the full pipeline
    - In `energy_cost_predictor/example.py` build a `main()` that constructs placeholder providers + MA region, runs an annual `predict_annual_cost` and `compare_savings`, runs `Calibration`, and prints the `AccuracyReport` — no hanging/unintegrated code
    - _Requirements: 1.1, 1.3, 7.1_

  - [ ]* 11.2 Write integration test proving provider substitution
    - Run an identical prediction scenario through the placeholder provider and a second stub exposing the same Protocol; assert identical results with zero changes to any core module source
    - _Requirements: 2.4, 2.5_

  - [ ]* 11.3 Write end-to-end integration test for annual prediction and savings
    - Assert the predict call completes within the 5-second budget and that cost/savings values fall within 0.00–999,999.99
    - _Requirements: 1.1, 7.4_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirements and, where applicable, the exact correctness Property it validates.
- Property-based tests use `hypothesis` with the required tag comment and `@settings(max_examples=100)` (or higher); unit tests cover the Error Handling table and per-fuel examples; integration tests prove provider substitution (Req 2.4/2.5).
- The pure core modules (`demand.py`, `heat_pump.py`, `seasonal_rate.py`, `cost.py`, `accuracy.py`) import no providers and perform no I/O, which structurally guarantees Req 2.5.
- Checkpoints provide incremental validation at natural layer boundaries.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["2.1", "3.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "3.2", "4.1", "6.2"] },
    { "id": 4, "tasks": ["2.4", "4.2", "4.3", "4.4", "4.5", "6.1", "6.5"] },
    { "id": 5, "tasks": ["6.3", "6.4", "7.1", "7.2"] },
    { "id": 6, "tasks": ["7.3", "7.4", "9.1", "9.2"] },
    { "id": 7, "tasks": ["9.3", "9.4", "10.1", "10.2"] },
    { "id": 8, "tasks": ["10.3", "10.4", "10.5", "11.1"] },
    { "id": 9, "tasks": ["11.2", "11.3"] }
  ]
}
```
