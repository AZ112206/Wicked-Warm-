# Implementation Plan: Full-Stack Integration

## Overview

This plan wires Wicked Warm's three layers into one linear workflow. Tasks are ordered so each builds on the last: the Python_Service comes first (the Node_Server depends on it), then the Node_Server client/mapping/wiring, then the frontend `fetch()` wiring, with cross-cutting error mapping and secret handling woven in at the layer that owns each concern. Property-based tests for the nine design Correctness Properties and unit tests for the error-mapping tables are placed as sub-tasks next to the code they validate (`pytest` for Python, `node --test` for Node). A final end-to-end task boots both processes and confirms the full flow.

## Tasks

- [x] 1. Build the Python_Service HTTP boundary over the engine
  - [x] 1.1 Create `energy_cost_predictor/service.py` server skeleton and provider construction
    - Create a `ThreadingHTTPServer` + `BaseHTTPRequestHandler` subclass dispatching on method + path for `POST /predict`, `POST /savings`, `GET /prices`, `GET /regions`; serialize every response as JSON with `application/json` content type; make it runnable as `python -m energy_cost_predictor.service` on a configurable port (default 8000)
    - Implement `build_predictor()`: default `OpenMeteoWeatherProvider` + `EIAPricingProvider`; wrap each live provider in a guarding provider that delegates to the matching placeholder on any exception (including `ProviderTimeoutError`/`ProviderDataError`) and flips the matching `fallbacks` flag; if `EIA_API_KEY` is missing/empty/whitespace, use `PlaceholderPricingProvider` from the start with `fallbacks.pricing = true`; read `EIA_API_KEY` from the environment only
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.7, 8.1, 8.2, 8.3, 8.4_
    - _Design: Python_Service (service.py), Provider construction and per-source fallback_

  - [x] 1.2 Implement the `POST /predict` operation with optional calibration
    - Parse `house` (HouseProfile), `period` (Period), optional `reference_actual_usd`; call `Predictor.predict_annual_cost`; when `reference_actual_usd` is a number > 0, run `Calibration(...).calibrate()` (committing `demand_scale` onto the predictor) and set `calibration_applied = true`; on `InvalidActualCostError`/`ProviderTimeoutError` return the uncalibrated prediction with `calibration_applied = false`
    - Return `{cost_usd, missing_intervals (ISO-8601 strings), calibration_applied, fallbacks}`
    - _Requirements: 2.1, 2.5, 4.2, 4.5, 8.5, 8.6_
    - _Design: Prediction with optional calibration (Req 4)_

  - [x] 1.3 Implement the `POST /savings` operation
    - Parse `house` + `period`; call `Predictor.compare_savings`; return `{conventional_cost, heat_pump_cost, savings, fallbacks}` each rounded to two decimal places
    - _Requirements: 2.2, 2.6, 8.5, 8.6_
    - _Design: Components and Interfaces (Python_Service operation table)_

  - [x] 1.4 Implement the `GET /prices` and `GET /regions` synthesis operations
    - `GET /prices?metric=&months=`: support `electricity` (`¢/kWh`) and `gas` (`$/therm`), mapping to `electricity_per_kwh`×100 and `natural_gas_per_therm`; anchor a trailing `months`-long deterministic seasonal series ending at the most recent month so the final point equals the current price; return `{metric, unit, points, fallbacks}` with exactly `months` ordered points, each a non-empty month label and value ≥ 0
    - `GET /regions`: return the real Massachusetts entry (`rate` in ¢/kWh, `change` from the last two synthesized points) plus labeled placeholder regions (Northeast/Midwest/South/West) from fixed offsets, each rate ≥ 0, with `fallbacks.regions_placeholder = true`; shape `{regions: [{region, rate, change}], fallbacks}`
    - _Requirements: 2.3, 2.4, 6.1, 6.2, 6.3, 8.5, 8.6_
    - _Design: Price series and regions synthesis (Req 6)_

  - [x] 1.5 Implement engine-exception → HTTP status mapping
    - Map `MissingFieldError`/`InvalidPeriodError`/`MissingRegionKeyError`/`UnknownRegionError`/`UnsupportedFuelError`/`EfficiencyOutOfRangeError` and unsupported `metric` / out-of-1–60 `months` to HTTP 400 with a JSON error naming the field/reason/key; map `DependencyUnavailableError` and post-fallback `ProviderTimeoutError`/`ProviderDataError` to HTTP 502
    - _Requirements: 2.8, 2.9, 2.10, 8.7, 9.2, 9.3_
    - _Design: Error Handling (Engine exception → HTTP status)_

  - [x] 1.6 Checkpoint - Python_Service tests pass
    - Ensure all tests pass, ask the user if questions arise.

- [ ] 2. Test the Python_Service engine boundary
  - [ ] 2.1 Write unit tests for the engine-exception → HTTP mapping table
    - One representative test per row of the mapping table (400 cases name the field/key; 502 cases for dependency/post-fallback failures); assert status + JSON error shape
    - _Requirements: 2.8, 2.9, 2.10, 8.7, 9.2, 9.3_
    - _Design: Error Handling (Engine exception → HTTP status)_

  - [ ]* 2.2 Write unit tests for the calibration path
    - Valid bill → `calibration_applied: true`; rejected bill / provider timeout → `calibration_applied: false` with a prediction still returned
    - _Requirements: 4.2, 4.5_
    - _Design: Prediction with optional calibration (Req 4)_

  - [ ]* 2.3 Write property test for fallback-path shape equivalence
    - **Property 4: Fallback path produces the same JSON shape as the live path**
    - Mock raising live providers; assert placeholder-path field names and nesting match the live path, differing only in `fallbacks` values
    - **Validates: Requirements 8.5**

  - [ ]* 2.4 Write property test for the price series
    - **Property 6: Price series is well-formed for every supported request**
    - For any supported metric and `months` in 1–12: exactly `months` ordered points ending at the most recent month, non-empty unit label, each point non-empty label and value ≥ 0
    - **Validates: Requirements 6.1, 6.2**

  - [ ]* 2.5 Write property test for the regions list
    - **Property 7: Regions list entries are well-formed**
    - Every entry has a non-empty region name and a numeric rate ≥ 0
    - **Validates: Requirements 6.3**

- [x] 3. Build the Node_Server Python client and form/response mapping
  - [x] 3.1 Implement `src/pythonClient.js` HTTP client with timeout and guards
    - Provide `predict`, `savings`, `prices`, `regions`; send JSON bodies with `Content-Type: application/json`; 30s socket timeout plus a 10s watchdog; throw tagged errors — `ServiceUnavailable` on abort/unreachable, `BadUpstreamBody` on unparseable success body (without forwarding the raw body), `UpstreamError` carrying the upstream `error` on non-success (status ≥ 500 → 502; upstream 400 passthrough)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 9.6_
    - _Design: pythonClient.js contract behavior (Req 7)_

  - [x] 3.2 Implement `src/mapping.js` form → HouseProfile + Period mapping
    - Map `location` (non-empty → `region_key="MA"`; empty/missing is rejected upstream); derive `heated_area_sqft` via the design formula (base by home type + 250/person over 2, clamped to `(0, 100000]`), defaulting to `1500` when `homeType` or `household` is missing; default `setpoint_f=68`, `conventional_fuel="natural_gas"`, `system_efficiency=0.9`, `house_id="web-session"`, `ua_coefficient=0.0`; derive a 12-consecutive-month `Period` with `end > start`; include `reference_actual_usd` only when the bill amount is a number in `[0.01, 999999.99]`
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.3, 4.4_
    - _Design: Home-form → HouseProfile + Period mapping_

  - [x] 3.3 Implement the engine-result → forecast payload adapter in `src/mapping.js`
    - Adapt a non-negative-`cost_usd` predict response into `{amount (cost_usd/12, 2dp), lastMonth, currency: "USD", series:[{label,value,forecast}]}` with six trailing seasonal monthly points (final `forecast: true`); add `calibrated: true` when `calibration_applied` is true; never emit mock cost data on error or missing `cost_usd`
    - _Requirements: 3.8, 3.9, 4.6_
    - _Design: Engine result → frontend forecast payload_

  - [ ]* 3.4 Write property test for the form → HouseProfile mapping
    - **Property 1: Form mapping always yields a complete, valid HouseProfile**
    - For any non-empty location and any mix of present/absent optional fields: `region_key="MA"`, `0 < heated_area_sqft ≤ 100000`, `setpoint_f > 0`, fuel defined for MA, `0 < system_efficiency ≤ 1`
    - **Validates: Requirements 3.1, 3.3, 3.5, 3.6**

  - [ ]* 3.5 Write property test for the derived period
    - **Property 2: Derived period is always a valid 12-month annual range**
    - For any "now" date, `end > start` and spans exactly 12 consecutive months
    - **Validates: Requirements 3.7**

  - [ ]* 3.6 Write property test for the bill-amount validation boundary
    - **Property 3: Bill-amount validation boundary**
    - `reference_actual_usd` is included iff the value is a number in `[0.01, 999999.99]`; out-of-range/non-numeric never rejects the submission
    - **Validates: Requirements 4.1, 4.3, 4.4**

  - [ ]* 3.7 Write property test for the forecast-payload adapter
    - **Property 5: Non-negative cost maps to a forecast payload carrying that cost**
    - For any predict response with non-negative `cost_usd`, the payload has a numeric `amount` derived from `cost_usd` and conforms to `{amount, lastMonth, currency, series}`
    - **Validates: Requirements 3.8**

  - [ ]* 3.8 Write unit tests for the pythonClient guards
    - Mock fetch: success-with-bad-body → 502; non-success ≥ 500 → 502; upstream 400 → 400 passthrough; unreachable/timeout → 502
    - _Requirements: 7.3, 7.4, 7.5, 7.6_
    - _Design: pythonClient.js contract behavior (Req 7)_

- [x] 4. Wire the Node_Server Express application and routes
  - [x] 4.1 Add express dependency, serve script, and boot validation
    - Add `"express"` pinned to an exact version under `dependencies` in `package.json` and a `"serve": "node src/server.js"` script; create `src/server.js` that validates `GEMINI_API_KEY` at boot via `src/config.js` (terminating without accepting connections when missing) and reads both secrets from the environment only; wire `express.json()`, static middleware for `frontend/`, a 404 handler returning `{error:"Route not found"}`, and a central error handler emitting JSON `{error}` that never includes either secret
    - _Requirements: 1.1, 1.4, 1.5, 1.6, 1.7, 9.4_
    - _Design: New dependency and boot; Node_Server (src/server.js)_

  - [x] 4.2 Implement `POST /api/predict` and `POST /api/recommendations` handlers
    - Predict: validate non-empty `location` (else 400 naming each missing field within 2s, no partial profile), map via `mapping.js`, call `pythonClient.predict`, adapt to the forecast payload, map Python_Service errors to 400/502 per the table, never return mock cost data
    - Recommendations: require `prediction.amount` (else 400), build a prompt embedding the predicted cost, call `ask()` from `src/ask.js`, parse/validate 1–10 `{title, body}` items (non-empty title ≤120, body ≤2000), map Gemini failure/timeout to 502 excluding the key
    - _Requirements: 1.2, 1.3, 3.2, 3.8, 3.9, 4.6, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 9.1, 9.5_
    - _Design: Route handlers_

  - [x] 4.3 Implement `GET /api/prices` and `GET /api/prices/regions` handlers
    - Prices: validate supported `metric` and integer `months` in 1–12 (else 400 without a series), call `pythonClient.prices`, return `{metric, unit, points}`, map Python_Service unavailable to 502
    - Regions: call `pythonClient.regions`, return `[{region, rate, change}]`, map Python_Service unavailable to 502
    - _Requirements: 1.2, 1.8, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
    - _Design: Route handlers_

  - [x] 4.4 Checkpoint - Node_Server routes and tests pass
    - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Test the Node_Server route validation, action plan, and secret handling
  - [ ]* 5.1 Write unit tests for route validation
    - Missing/empty location → 400 naming the field; recommendations without predicted cost → 400; unsupported metric → 400; bad/missing months → 400; unknown route → 404
    - _Requirements: 1.3, 1.4, 3.2, 5.2, 6.4, 6.5, 9.1_
    - _Design: Node_Server error conditions_

  - [ ]* 5.2 Write property test for action plan validation
    - **Property 8: Action plan validation accepts exactly well-formed plans**
    - Returns the plan iff 1–10 items each with non-empty title ≤120 chars and non-empty body ≤2000 chars; otherwise an error indicating a valid action plan could not be generated
    - **Validates: Requirements 5.4, 5.5**

  - [ ]* 5.3 Write property test for secret exclusion
    - **Property 9: Secrets never appear in any response**
    - For any request on success and error paths, neither the Gemini key nor `EIA_API_KEY` value appears in any response body or header
    - **Validates: Requirements 1.7**

- [x] 6. Wire the frontend to the live Node endpoints
  - [x] 6.1 Replace the home-page stubs in `frontend/home/home.js`
    - Replace `fetchPrediction` and `fetchRecommendations` with real `fetch()` calls to `POST /api/predict` and `POST /api/recommendations`, throwing on non-ok using the JSON `error` field and preserving the exact `{amount, lastMonth, currency, series}` and `[{title, body}]` shapes the renderers consume (no renderer changes; existing error path satisfies Req 9.5)
    - _Requirements: 3.8, 5.1, 9.5_
    - _Design: Frontend fetch wiring (home.js)_

  - [x] 6.2 Replace the price-tracker stubs in `frontend/price-tracker/price-tracker.js`
    - Replace `fetchPriceSeries` and `fetchRegionalPrices` with real `fetch()` calls to `GET /api/prices?metric=&months=` and `GET /api/prices/regions`, throwing on non-ok using the JSON `error` field and preserving the exact `{metric, unit, points}` and `[{region, rate, change}]` shapes
    - _Requirements: 6.1, 6.2, 6.3, 9.5_
    - _Design: Frontend fetch wiring (price-tracker.js)_

- [x] 7. End-to-end verification
  - [x] 7.1 Boot both processes and confirm the full linear flow
    - Start the Python_Service (`python -m energy_cost_predictor.service`) and the Node_Server (`npm run serve`); submit the home form and confirm a real forecast renders plus a Gemini action plan; load the price-tracker and confirm live/fallback price series and regional data render; confirm an induced Python_Service outage surfaces a 502 error in the UI with no forecast
    - _Requirements: 1.1, 1.2, 3.8, 5.1, 6.1, 6.3, 9.5, 9.6_
    - _Design: Architecture (Request-flow diagram)_

## Notes

- Tasks marked with `*` are optional test/polish sub-tasks and can be skipped for a faster MVP; the core linear-workflow wiring (unmarked tasks) is required.
- Each task references the specific requirement numbers and the design section it implements for traceability.
- Property tests run ≥ 100 iterations and are tagged `Feature: full-stack-integration, Property N: <text>`; `pytest` hosts the Python properties and `node --test` hosts the Node/mapping properties.
- Checkpoints ensure incremental validation at each layer boundary.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "2.3", "2.4", "2.5"] },
    { "id": 3, "tasks": ["2.1", "2.2", "3.1", "3.2"] },
    { "id": 4, "tasks": ["3.3", "3.4", "3.5", "3.6", "3.8"] },
    { "id": 5, "tasks": ["3.7", "4.1"] },
    { "id": 6, "tasks": ["4.2", "4.3"] },
    { "id": 7, "tasks": ["5.1", "5.2", "5.3", "6.1", "6.2"] },
    { "id": 8, "tasks": ["7.1"] }
  ]
}
```
