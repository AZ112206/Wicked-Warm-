# Design Document

## Overview

This feature wires Wicked Warm's three disconnected parts into one linear workflow: the static `frontend/` pages, the Python `energy_cost_predictor` engine, and the Node Gemini helper in `src/ask.js`. Two new processes are introduced.

- **Node_Server** — a Node.js Express process. It serves the static `frontend/` directory, exposes four JSON API endpoints (`POST /api/predict`, `POST /api/recommendations`, `GET /api/prices`, `GET /api/prices/regions`), holds all secrets (`GEMINI_API_KEY`, `EIA_API_KEY`) server-side, maps browser form input onto the engine's request shapes, calls the Python_Service over HTTP for cost/price computation, and calls Gemini (via the existing `ask.js`) for the AI action plan.
- **Python_Service** — a Python HTTP microservice built on the standard-library `http.server`. It wraps the `energy_cost_predictor` engine (`Predictor`, `Calibration`, providers, region registry) and exposes four operations: predict, savings, prices, regions. It constructs a `Predictor` with live providers (`OpenMeteoWeatherProvider`, `EIAPricingProvider`) and falls back to placeholder providers per-source when a live call fails or `EIA_API_KEY` is missing.

The browser never holds API keys. The target end-to-end flow is: **frontend form submit → Node_Server `/api/*` → Python_Service (prediction / savings / prices) and Gemini via Node (action plan) → results back to the frontend.** The frontend's four mock stubs are replaced with real `fetch()` calls that preserve the exact response shapes the existing rendering code expects.

Addresses Requirements 1–9.

### Design goals and constraints

- **Secrets stay server-side** (Req 1.5, 1.7). Only the Node_Server reads `GEMINI_API_KEY`; only the Python_Service reads `EIA_API_KEY`. Neither key appears in any response body or header.
- **Preserve frontend response shapes exactly** (Req 3.8, 6.1–6.3). The Node_Server adapts engine output into the four shapes the existing renderers in `home.js` and `price-tracker.js` consume, so no rendering code changes beyond swapping the stub bodies for `fetch()`.
- **Minimal new dependencies.** Add `express` (pinned) to `package.json`. The Python_Service uses only the Python standard library (`http.server`), avoiding a heavy web framework — the engine's own providers already use `urllib` only, so no new Python dependency is introduced.
- **Live-first with graceful fallback** (Req 8). Live providers are the default; placeholder providers are substituted per-source on failure or missing key, and the response shape is identical on both paths.

### New dependency and boot

- `package.json` gains `"express"` pinned to an exact version (e.g. `"express": "4.21.2"`) under `dependencies`, and a new script `"serve": "node src/server.js"`. The server boots by validating `GEMINI_API_KEY` is present (reusing the existing `src/config.js` validation, which calls `process.exit(1)` when the key is missing — satisfying Req 1.6), then listening on a configurable port (default 3000).
- The Python_Service boots via a new module `energy_cost_predictor/service.py` runnable as `python -m energy_cost_predictor.service` on a configurable port (default 8000). The Node_Server reaches it at a base URL from the `PYTHON_SERVICE_URL` environment variable (default `http://127.0.0.1:8000`).

## Architecture

Two processes communicate over localhost HTTP. The browser talks only to the Node_Server.

### Request-flow diagram

```
┌─────────────┐        ┌──────────────────────────────┐        ┌──────────────────────────┐
│   Browser   │        │   Node_Server (Express)       │        │  Python_Service          │
│ home.js     │        │   src/server.js               │        │  http.server             │
│ price-      │        │   secrets: GEMINI_API_KEY      │        │  secret: EIA_API_KEY     │
│ tracker.js  │        │                                │        │  wraps energy_cost_      │
└─────┬───────┘        └───────────────┬────────────────┘        │  predictor engine        │
      │                                 │                         └────────────┬─────────────┘
      │  GET /home/index.html           │                                      │
      │────────────────────────────────▶│  static file (200) ──────────────▶   │
      │                                 │                                      │
      │  POST /api/predict (form JSON)  │                                      │
      │────────────────────────────────▶│  map form → HouseProfile + Period    │
      │                                 │  POST /predict (JSON, 30s timeout)   │
      │                                 │─────────────────────────────────────▶│ Predictor.predict_annual_cost
      │                                 │                                      │ (+ Calibration if bill amount)
      │                                 │  ◀───────────────────────────────────│ {cost_usd, missing_intervals,
      │                                 │  adapt → {amount,...,series}         │  calibration_applied, fallbacks}
      │  ◀──────────────────────────────│  200 forecast payload                │
      │                                 │                                      │
      │  POST /api/recommendations      │                                      │
      │────────────────────────────────▶│  build prompt from predicted cost    │
      │                                 │  ask(prompt) ──▶ Gemini (30s)         │
      │                                 │  ◀── action plan text                │
      │  ◀──────────────────────────────│  200 [{title, body}, ...]            │
      │                                 │                                      │
      │  GET /api/prices?metric=&months=│                                      │
      │────────────────────────────────▶│  GET /prices?metric=&months= ───────▶│ provider price series
      │                                 │  ◀───────────────────────────────────│ {metric, unit, points}
      │  ◀──────────────────────────────│  200 {metric, unit, points}          │
      │                                 │                                      │
      │  GET /api/prices/regions        │  GET /regions ──────────────────────▶│ region registry + prices
      │────────────────────────────────▶│  ◀───────────────────────────────────│ [{region, rate, change}]
      │  ◀──────────────────────────────│  200 [{region, rate, change}]        │
```

### Layering

| Layer | Responsibility | Secrets |
| --- | --- | --- |
| Browser (`frontend/`) | Collect form input, render results, display error text. Calls only Node_Server. | none |
| Node_Server (`src/server.js`) | Static file serving, input validation, form→engine mapping, Python_Service HTTP calls, Gemini call, response shaping, error mapping. | `GEMINI_API_KEY` |
| Python_Service (`energy_cost_predictor/service.py`) | HTTP boundary over the engine; provider selection + per-source fallback; engine-exception→HTTP mapping; price-series/regions synthesis. | `EIA_API_KEY` |
| Engine (`energy_cost_predictor/*`) | Pure prediction, savings, calibration, providers, region registry. Unchanged by this feature. | none |

### Timeouts

- Node_Server → Python_Service request timeout: **30 seconds** (Req 7.1). An additional watchdog treats "no response within 10 seconds or HTTP status ≥ 500" as a 502 to the frontend (Req 9.6). The 10-second watchdog fires first and is the effective forecast-service deadline; the 30-second value bounds the socket.
- Node_Server → Gemini: **30 seconds** (Req 5.4, 5.6).
- Engine provider deadline (inside Python_Service): **30 seconds** via the existing provider adapters (Req 4.5, 8.3, 8.4).

## Components and Interfaces

### Node_Server (`src/server.js`)

Express application with the following middleware/route order:

1. JSON body parser (`express.json()`).
2. API routes under `/api`.
3. Static middleware for `frontend/` (`express.static`), so `GET /home/index.html`, `/price-tracker/price-tracker.html`, etc. resolve to files (Req 1.1).
4. A catch-all 404 handler returning `{ "error": "Route not found" }` with status 404 (Req 1.4).
5. A central error handler that emits `{ "error": <message> }` as JSON and never includes either secret (Req 1.7, 9.4).

Internal modules:

- `src/server.js` — Express wiring, route handlers.
- `src/pythonClient.js` — thin HTTP client to the Python_Service (`predict`, `savings`, `prices`, `regions`), enforcing the 30s timeout and JSON parse guard (Req 7.1–7.6).
- `src/mapping.js` — pure functions mapping the home form to a `HouseProfile` + `Period`, and adapting engine responses into frontend shapes.
- `src/ask.js` — existing Gemini helper, reused unchanged for the action plan.
- `src/config.js` — existing config loader, reused for `GEMINI_API_KEY` validation at boot (Req 1.6).

#### Route handlers

| Route | Handler behavior |
| --- | --- |
| `POST /api/predict` | Validate home form (location non-empty → else 400 naming `location`; Req 3.2, 9.1). Map to `HouseProfile` + `Period` via `mapping.js`. If a valid bill amount is present, include `reference_actual_usd` (Req 4.1, 4.3, 4.4). Call `pythonClient.predict`. Adapt the engine result to the forecast payload (Req 3.8, 4.6). On Python_Service error → 502 or 400 per the error table (Req 3.9). |
| `POST /api/recommendations` | Require `prediction.amount` (predicted cost) in the body → else 400 (Req 5.2). Build a prompt embedding the predicted cost (Req 5.3). Call `ask(prompt)`. Parse Gemini output into 1–10 `{title, body}` items; validate each item (non-empty title ≤120 chars, non-empty body ≤2000 chars) → else error (Req 5.4, 5.5). Gemini failure/timeout → 502 excluding the key (Req 5.6). |
| `GET /api/prices` | Validate `metric` is supported and `months` is an integer in 1–12 → else 400 (Req 6.4, 6.5). Call `pythonClient.prices(metric, months)`. Return `{metric, unit, points}` (Req 6.1, 6.2). Python_Service unavailable → 502 (Req 6.6). |
| `GET /api/prices/regions` | Call `pythonClient.regions`. Return `[{region, rate, change}]` (Req 6.3). Python_Service unavailable → 502 (Req 6.6). |

#### `pythonClient.js` contract behavior (Req 7)

- Sends request bodies as JSON with `Content-Type: application/json` (Req 7.2).
- Aborts the request at 30 seconds; on abort/unreachable → throws a tagged `ServiceUnavailable` error the handler maps to 502 (Req 7.6, 9.6).
- On a success status with a parseable JSON body → returns the parsed object (Req 7.3).
- On a success status with an unparseable body → throws `BadUpstreamBody` → 502, without forwarding the raw body (Req 7.4).
- On a non-success status (including ≥ 500) → throws `UpstreamError` carrying the upstream `error` message → 502 (Req 7.5, 9.6). A Python_Service 400 is surfaced to the frontend as 400 with the upstream message (validation passthrough for Req 2.8–2.10, 9.2, 9.3).

### Python_Service (`energy_cost_predictor/service.py`)

A `http.server.ThreadingHTTPServer` with a `BaseHTTPRequestHandler` subclass dispatching on method + path. All responses are JSON with `Content-Type: application/json` (Req 2.7).

| Operation | HTTP | Request | Response (200) |
| --- | --- | --- | --- |
| Predict | `POST /predict` | `{house, period, reference_actual_usd?}` | `{cost_usd, missing_intervals, calibration_applied, fallbacks}` |
| Savings | `POST /savings` | `{house, period}` | `{conventional_cost, heat_pump_cost, savings, fallbacks}` |
| Prices | `GET /prices?metric=&months=` | query params | `{metric, unit, points, fallbacks}` |
| Regions | `GET /regions` | — | `{regions: [{region, rate, change}], fallbacks}` |

#### Provider construction and per-source fallback (Req 8)

The service builds a `Predictor` per request using a helper `build_predictor()`:

- **Weather**: default `OpenMeteoWeatherProvider` (no key). If a call raises or times out, substitute `PlaceholderWeatherProvider` for weather only and record `fallbacks.weather = true` (Req 8.3).
- **Pricing**: default `EIAPricingProvider`. If `EIA_API_KEY` is missing/empty/whitespace, use `PlaceholderPricingProvider` from the start and record `fallbacks.pricing = true` (Req 8.2). If the live call raises or times out, substitute `PlaceholderPricingProvider` for pricing only (Req 8.4).
- **Reference actual** (calibration path): `PlaceholderReferenceActualProvider` seeded with the submitted bill amount; see below.

Fallback is implemented by wrapping each live provider in a **guarding provider** that delegates to the live provider and, on any exception (including the adapter's `ProviderTimeoutError`/`ProviderDataError`), delegates to the placeholder and flips the corresponding `fallbacks` flag. Because the guarding provider returns the same `WeatherSeries`/`PriceTable` dataclasses as the live provider, the engine and the serialized JSON field names and nesting are identical on both paths (Req 8.5). The `fallbacks` object is attached to every response so the caller can see which source fell back (Req 8.6).

#### Prediction with optional calibration (Req 4)

```
POST /predict
  parse house (HouseProfile), period (Period), optional reference_actual_usd
  predictor = build_predictor()
  if reference_actual_usd is a number > 0:
      ref_provider = PlaceholderReferenceActualProvider(reference_actual_usd)
      try:
          Calibration(predictor, house, period, ref_provider).calibrate()   # commits demand_scale onto predictor
          calibration_applied = True
      except (InvalidActualCostError, ProviderTimeoutError):
          calibration_applied = False          # fall back to uncalibrated (Req 4.5)
  else:
      calibration_applied = False
  result = predictor.predict_annual_cost(house, period)
  return {cost_usd: result.cost_usd,
          missing_intervals: [d.isoformat() for d in result.missing_intervals],
          calibration_applied, fallbacks}
```

`Calibration.calibrate()` tunes `demand_scale` and commits the calibrated params onto the same `predictor` instance, so the subsequent `predict_annual_cost` call uses the calibrated parameters (Req 4.2). A rejected reference actual or a provider timeout yields a prediction computed without calibration and `calibration_applied = false` (Req 4.5).

#### Price series and regions synthesis (Req 6)

The engine exposes *point* prices per region through `PricingProvider.get_prices(region_key)`; it does not expose a historical monthly series, and only `MA` is configured. The Python_Service derives the shapes the frontend needs as follows.

- **Supported metrics**: `electricity` (unit `¢/kWh`) and `gas` (unit `$/therm`). Any other `metric` → 400 (Req 2.10, 6.4). These map to price-table keys `electricity_per_kwh` (dollars/kWh, multiplied by 100 to cents) and `natural_gas_per_therm`.
- **Monthly series**: the service reads the current point price for the metric from the active pricing provider (live `EIAPricingProvider` or placeholder on fallback), then anchors a trailing `months`-long synthetic series ending at the most recent month. The series is produced by a deterministic seasonal shape (a fixed per-month multiplier table for heating season, same approach as the engine's synthetic weather curve) scaled so the final point equals the anchored current price. This yields exactly `months` ordered points, each with a non-empty month label (`"Nov"`, `"Dec"`, …) and a value ≥ 0, ending with the most recent month (Req 6.1, 6.2). The service documents in a comment that only the latest point is a real observation; earlier points are a derived trailing estimate.
- **Regions list**: the engine configures only `MA` as a real region (`REGION_REGISTRY`). The service returns one real entry for Massachusetts built from the live/placeholder electricity price (`rate` in ¢/kWh, `change` derived as the percent delta between the last two synthesized monthly points) plus clearly-labeled placeholder entries for the other display regions the frontend table expects (Northeast/Midwest/South/West), each a non-negative number (Req 6.3). Placeholder regions are generated from fixed offsets off the MA rate and flagged via `fallbacks.regions_placeholder = true`. This keeps the frontend `[{region, rate, change}]` shape intact while honoring that MA is the only engine-configured region.

### Frontend fetch wiring

The four stubs are replaced with real calls; no renderer changes. Response shapes are preserved exactly.

#### `home.js`

```js
async function fetchPrediction(userInput) {
  const res = await fetch("/api/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(userInput),
  });
  if (!res.ok) throw new Error((await res.json()).error || "Forecast failed");
  return await res.json();   // {amount, lastMonth, currency, series:[{label,value,forecast}]}
}

async function fetchRecommendations(prediction, userInput) {
  const res = await fetch("/api/recommendations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prediction, userInput }),
  });
  if (!res.ok) throw new Error((await res.json()).error || "Recommendations failed");
  return await res.json();   // [{title, body}, ...]
}
```

The existing submit handler already surfaces thrown errors via `showError(...)` and does not render a forecast on failure, satisfying Req 9.5. The error message shown is taken from the Node_Server JSON `error` field.

#### `price-tracker.js`

```js
async function fetchPriceSeries(metric, months) {
  const res = await fetch(`/api/prices?metric=${encodeURIComponent(metric)}&months=${months}`);
  if (!res.ok) throw new Error((await res.json()).error || "Prices unavailable");
  return await res.json();   // {metric, unit, points:[{label,value}]}
}

async function fetchRegionalPrices() {
  const res = await fetch("/api/prices/regions");
  if (!res.ok) throw new Error((await res.json()).error || "Regions unavailable");
  return await res.json();   // [{region, rate, change}]
}
```

## Data Models

### Node ↔ Python JSON schemas

**HouseProfile (request field `house`)** — mirrors `energy_cost_predictor.models.HouseProfile`:

```json
{
  "house_id": "web-session",
  "region_key": "MA",
  "heated_area_sqft": 1800,
  "ua_coefficient": 0.00004,
  "setpoint_f": 68,
  "conventional_fuel": "natural_gas",
  "system_efficiency": 0.9
}
```

**Period (request field `period`)** — mirrors `energy_cost_predictor.predictor.Period`, dates as ISO-8601 strings:

```json
{ "start": "2024-11-01", "end": "2025-10-31" }
```

`end` is exactly 12 consecutive months after `start` and later than `start` (Req 3.7), satisfying the engine's annual-period check used by `compare_savings` and accepted by `predict_annual_cost`.

**Predict response**:

```json
{ "cost_usd": 2184.40, "missing_intervals": ["2024-11-18"], "calibration_applied": true,
  "fallbacks": { "weather": false, "pricing": false } }
```

**Savings response**:

```json
{ "conventional_cost": 2184.40, "heat_pump_cost": 1510.22, "savings": 674.18,
  "fallbacks": { "weather": false, "pricing": false } }
```

**Prices response**:

```json
{ "metric": "electricity", "unit": "¢/kWh",
  "points": [ { "label": "Nov", "value": 16.9 }, { "label": "Dec", "value": 17.8 } ],
  "fallbacks": { "pricing": false } }
```

**Regions response**:

```json
{ "regions": [ { "region": "Massachusetts", "rate": 17.0, "change": 1.2 } ],
  "fallbacks": { "pricing": false, "regions_placeholder": true } }
```

### Home-form → HouseProfile + Period mapping

The browser posts the `userInput` object already assembled in `home.js` (`location`, `household`, `homeType`, `income`, `billFileNames`) plus any parsed bill amount. The Node_Server maps it in `mapping.js`:

| Form field | HouseProfile / request field | Rule |
| --- | --- | --- |
| `location` (non-empty) | `region_key` | Always set to `"MA"` (the only configured region). Empty/missing → 400, no partial profile (Req 3.1, 3.2). |
| `homeType` + `household` | `heated_area_sqft` | Derived (see formula). Always `> 0` and `≤ 100000` (Req 3.3). Missing either → default `1500` (Req 3.4). |
| (none) | `setpoint_f` | Default `68` °F (positive Fahrenheit) (Req 3.5). |
| (none) | `conventional_fuel` | Default `"natural_gas"` (a fuel defined for `MA`) (Req 3.5). |
| (none) | `system_efficiency` | Default `0.9` (in `(0, 1]`) (Req 3.5). |
| (none) | `house_id` | Constant `"web-session"`. |
| (none) | `ua_coefficient` | `0.00004` — a positive per-sqft heat-loss seed (engine seed; not a required field). The engine computes `UA_effective = ua_coefficient * heated_area_sqft * demand_scale`, so a `0.0` seed would zero every forecast; this mirrors the engine reference example and lets calibration tune `demand_scale` from a non-zero baseline. |
| bill amount | `reference_actual_usd` | Included only when a number in `[0.01, 999999.99]`; otherwise omitted without rejecting the submission (Req 4.1, 4.3, 4.4). |

**`heated_area_sqft` derivation formula** (deterministic, always within `(0, 100000]`):

```
base_by_home_type = { "apartment": 900, "condo": 1100, "townhouse": 1500,
                      "single_family": 2000, "other": 1500 }
base = base_by_home_type[homeType]  if homeType present, else 1500
per_person = 250
raw = base + per_person * max(0, household - 2)       # 2 occupants assumed in base
heated_area_sqft = min(100000, max(1, round(raw)))    # clamp into (0, 100000]
```

When `homeType` or `household` is missing, `heated_area_sqft` defaults to `1500` (Req 3.4). The result is guaranteed `> 0` and `≤ 100000` (Req 3.3).

**Period derivation**: `start` = first day of the month 11 months before the current month; `end` = last day of the current month, giving exactly 12 consecutive months with `end > start` (Req 3.7).

### Engine result → frontend forecast payload

`home.js` expects `{amount, lastMonth, currency, series:[{label,value,forecast}]}`. The Node_Server adapts the predict response:

- `amount` = monthly figure derived from `cost_usd` (annual ÷ 12, rounded to 2dp). `currency` = `"USD"`.
- `lastMonth` = the second-to-last monthly series value, used by the renderer for the trend arrow.
- `series` = six trailing monthly points labeled by month; the final point carries `forecast: true`. Points are a seasonal split of the annual `cost_usd` (same seasonal shape used for the price series), so the sum/shape is consistent with the computed annual cost. When `calibration_applied` is true, an extra flag (e.g. `calibrated: true`) is included so the page can indicate the uploaded bill was used (Req 4.6).

On any Python_Service error or a missing `cost_usd`, the Node_Server returns an error payload and never returns mock cost data (Req 3.9).

## Error Handling

### Engine exception → HTTP status (Python_Service)

| Engine exception (`errors.py`) | Python_Service status | Node_Server status to frontend |
| --- | --- | --- |
| `MissingFieldError` | 400 (names field) (Req 2.8, 9.2) | 400 (passthrough message) |
| `InvalidPeriodError` | 400 (names reason) (Req 2.8, 9.2) | 400 |
| `MissingRegionKeyError` | 400 (Req 2.9) | 400 |
| `UnknownRegionError` | 400 (names key) (Req 2.9, 9.3) | 400 |
| `UnsupportedFuelError` / `EfficiencyOutOfRangeError` | 400 | 400 |
| Unsupported `metric` (price request) | 400 (Req 2.10, 6.4) | 400 (Req 6.4) |
| Invalid `months` (out of 1–60 service range) | 400 (Req 2.3) | handled at Node for 1–12 before call (Req 6.5) |
| `DependencyUnavailableError` | 502 (Req 1.8) | 502 |
| `ProviderTimeoutError` / `ProviderDataError` (after fallback still failing) | 502 (Req 8.7) | 502 |
| `InvalidActualCostError` (calibration) | not an error response — prediction returned uncalibrated (Req 4.5) | 200 with `calibration_applied:false` |

### Node_Server error conditions

| Condition | Status | Body | Requirement |
| --- | --- | --- | --- |
| Missing/empty `location` on predict | 400 | `{error}` naming location, within 2s | 3.2, 9.1 |
| Missing required form field(s) | 400 | `{error}` naming each missing field | 9.1 |
| Recommendations without predicted cost | 400 | `{error}` | 5.2 |
| Unsupported `metric` | 400 | `{error}` metric unsupported | 6.4 |
| Missing/out-of-range `months` (not 1–12) | 400 | `{error}` months invalid | 6.5 |
| Invalid API route | 404 | `{error: "Route not found"}` | 1.4 |
| Gemini failure/timeout | 502 | `{error}` excluding the key | 5.6 |
| Python_Service unreachable / timeout / status ≥ 500 / unparseable body | 502 | `{error}` data source unavailable | 6.6, 7.4, 7.5, 7.6, 9.6 |
| Upstream data provider unavailable | 502 | `{error}` | 1.8 |

Every Node_Server error body is JSON with a non-empty `error` string and excludes both secrets (Req 1.7, 9.4). The frontend displays the `error` text and renders no forecast (Req 9.5).

## Testing Strategy

Both unit/example tests and property-based tests are used; they are complementary.

- **Python_Service**: unit tests for the engine-exception→HTTP mapping (one representative per row), the provider-fallback flag behavior (mock a raising live provider and assert the placeholder result + `fallbacks` flag), and the calibration path (valid bill → `calibration_applied: true`; rejected bill → `false`, prediction still returned). Property tests for the mapping and fallback-shape invariants below. The existing `tests/` suite (`pytest`, see `conftest.py`) is the host; new tests live alongside it.
- **Node_Server**: unit tests for `mapping.js` (form→HouseProfile defaults and the `heated_area_sqft` formula), the `pythonClient.js` parse/status guards (mock fetch returning success-with-bad-body → 502; non-success → 502; 400 → 400 passthrough), route validation (missing location → 400; bad months → 400; unknown route → 404), and the Gemini action-plan parsing/validation. Property tests for the mapping and bill-validation boundary below. A lightweight JS test runner (`node --test`) is used since none is configured today.
- **Frontend**: the renderers are unchanged; coverage is the shape contract asserted by the Node_Server tests (response objects match `{amount,...}`, `[{title,body}]`, `{metric,unit,points}`, `[{region,rate,change}]`).

Property tests run ≥ 100 iterations and are tagged `Feature: full-stack-integration, Property N: <text>`.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — a formal statement about what the system should do.*

Properties were consolidated after reflection: the `heated_area_sqft` range invariant (Req 3.3) and the default-field invariant (Req 3.5) are folded into the comprehensive mapping property (Property 1); the per-point constraints (Req 6.2) are folded into the price-series property (Property 6).

### Property 1: Form mapping always yields a complete, valid HouseProfile

*For any* home-form submission with a non-empty location and any combination of present or absent optional fields (homeType, household, setpoint, fuel, efficiency), the mapped HouseProfile has `region_key` equal to `"MA"`, `heated_area_sqft` greater than 0 and no greater than 100000, `setpoint_f` greater than 0, `conventional_fuel` among the fuels defined for region `MA`, and `system_efficiency` greater than 0 and no greater than 1 — so every required field is present and non-empty and the engine's required-field validation never raises.

**Validates: Requirements 3.1, 3.3, 3.5, 3.6**

### Property 2: Derived period is always a valid 12-month annual range

*For any* submission date used as "now", the derived Period has `end` later than `start` and spans exactly 12 consecutive months, so the engine's annual-period check accepts it.

**Validates: Requirements 3.7**

### Property 3: Bill-amount validation boundary

*For any* supplied bill-amount value, the prediction request includes a `reference_actual_usd` if and only if the value is a number within the inclusive range 0.01 to 999,999.99, and no bill-amount value outside that range (or non-numeric) causes the submission to be rejected.

**Validates: Requirements 4.1, 4.3, 4.4**

### Property 4: Fallback path produces the same JSON shape as the live path

*For any* prediction, savings, prices, or regions request, the response served through placeholder-provider fallback has field names and nesting structure identical to the response served through the live providers (differing only in the values of the `fallbacks` flags).

**Validates: Requirements 8.5**

### Property 5: Non-negative cost maps to a forecast payload carrying that cost

*For any* prediction response with a non-negative `cost_usd`, the adapted forecast payload contains a numeric `amount` derived from `cost_usd` and conforms to the `{amount, lastMonth, currency, series}` shape the frontend renders.

**Validates: Requirements 3.8**

### Property 6: Price series is well-formed for every supported request

*For any* supported metric and any `months` value in the inclusive range 1 to 12, the price series contains exactly `months` ordered monthly points ending with the most recent month, carries a non-empty unit label string, and every point has a non-empty month label string and a numeric value greater than or equal to 0.

**Validates: Requirements 6.1, 6.2**

### Property 7: Regions list entries are well-formed

*For any* regions response, every entry contains a non-empty region name string and a numeric current rate greater than or equal to 0.

**Validates: Requirements 6.3**

### Property 8: Action plan validation accepts exactly well-formed plans

*For any* candidate list of recommendation items parsed from the Gemini response, the endpoint returns the plan if and only if it has between 1 and 10 items where each item has a non-empty title of at most 120 characters and a non-empty body of at most 2000 characters; otherwise it returns an error indicating a valid action plan could not be generated.

**Validates: Requirements 5.4, 5.5**

### Property 9: Secrets never appear in any response

*For any* request to any endpoint, on both success and error paths, neither the Gemini API key value nor the EIA_API_KEY value appears in the response body or in any response header.

**Validates: Requirements 1.7**
