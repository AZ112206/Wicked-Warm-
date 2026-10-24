# Requirements Document

## Introduction

Wicked Warm today exists as three disconnected workstreams: a static frontend (home, price-tracker, about pages) whose data functions are stubs returning mock data; a Python energy-cost engine (`energy_cost_predictor`) that computes heating-cost predictions, savings comparisons, and prices from pluggable live/placeholder providers; and a Node Gemini helper (`src/ask.js`) that talks to the `@google/genai` model. None of these three parts calls any of the others.

This feature wires them into one linear workflow. A Node Express server serves the static frontend and exposes four JSON API endpoints. A separate Python HTTP microservice wraps the `energy_cost_predictor` engine and exposes prediction, savings, and price operations. The Node server maps frontend form input onto the engine's request shapes, calls the Python microservice for cost and price computation, calls Gemini (via the existing `ask.js` code) for an AI action plan derived from the prediction, and returns results to the browser. The browser never holds API keys; all secrets stay server-side. Live data providers (Open-Meteo weather, EIA pricing) are used with placeholder-provider fallback when a live call fails or a key is missing.

The target end-to-end flow is: frontend form submit → Node Express API → Python engine microservice (prediction / savings / prices) and Gemini via Node (action plan) → results back to the frontend. The price-tracker page and the regional-price listing are backed by the same Python microservice.

## Glossary

- **Node_Server**: The Node.js Express process that serves the static frontend and exposes the four `/api/*` HTTP endpoints. Holds all secrets.
- **Python_Service**: The Python HTTP microservice that wraps the `energy_cost_predictor` engine and exposes prediction, savings, and price operations over HTTP.
- **Engine**: The `energy_cost_predictor` package (`Predictor`, `Calibration`, providers, region registry) invoked in-process by the Python_Service.
- **Frontend**: The static browser pages (`home`, `price-tracker`) whose data functions call the Node_Server `/api/*` endpoints.
- **Predict_Endpoint**: The Node_Server route `POST /api/predict`.
- **Recommendations_Endpoint**: The Node_Server route `POST /api/recommendations`.
- **Prices_Endpoint**: The Node_Server route `GET /api/prices` accepting `metric` and `months` query parameters.
- **Regions_Endpoint**: The Node_Server route `GET /api/prices/regions`.
- **House_Profile**: The engine input record `HouseProfile(house_id, region_key, heated_area_sqft, ua_coefficient, setpoint_f, conventional_fuel, system_efficiency)`; its required fields are `region_key`, `heated_area_sqft`, `setpoint_f`, `conventional_fuel`, `system_efficiency`.
- **Period**: The engine input record `Period(start, end)` bounding an inclusive heating range by two dates.
- **Prediction_Result**: The engine output `EstimateResult(cost_usd, missing_intervals)` from `Predictor.predict_annual_cost`.
- **Savings_Result**: The engine output `SavingsResult(conventional_cost, heat_pump_cost, savings)` from `Predictor.compare_savings`.
- **Live_Weather_Provider**: `OpenMeteoWeatherProvider`, the live weather data source (no API key required).
- **Live_Pricing_Provider**: `EIAPricingProvider`, the live price data source requiring the `EIA_API_KEY` environment variable.
- **Placeholder_Provider**: A fallback weather or pricing provider used when a live provider call fails or a required key is absent.
- **Gemini_Client**: The existing `@google/genai` integration in `src/ask.js`, invoked by the Node_Server to generate the AI action plan.
- **Action_Plan**: The list of recommendation items (each with a title and body) returned by the Recommendations_Endpoint.
- **Default_Region_Key**: The region key `MA`, the only region configured in the Engine's region registry today.
- **EIA_API_KEY**: The environment variable holding the EIA pricing API key.

## Requirements

### Requirement 1: Node server serves frontend and API routes

**User Story:** As a homeowner, I want the Node server to serve the website and expose the forecast APIs, so that the browser loads the pages and talks only to my backend.

#### Acceptance Criteria

1. WHEN the Node_Server receives a GET request for a static Frontend file that exists in the `frontend` directory, THE Node_Server SHALL respond with HTTP status 200 and the file contents in the response body.
2. WHEN the Node_Server receives a valid request on the Predict_Endpoint, the Recommendations_Endpoint, the Prices_Endpoint, or the Regions_Endpoint, THE Node_Server SHALL respond with HTTP status 200 and the corresponding result data.
3. IF the Node_Server receives a request on an API endpoint with invalid or missing required parameters, THEN THE Node_Server SHALL respond with HTTP status 400 and an error message.
4. WHEN the Node_Server receives a request for an undefined route, THE Node_Server SHALL respond with HTTP status 404 and a response body indicating the route was not found.
5. THE Node_Server SHALL read the Gemini API key and the EIA_API_KEY from server-side environment variables only.
6. IF the Gemini API key is missing at startup, THEN THE Node_Server SHALL terminate without accepting connections.
7. THE Node_Server SHALL exclude the Gemini API key and the EIA_API_KEY from every HTTP response body and response header sent to the Frontend, on both success and error responses.
8. IF an upstream data provider required to serve an API request is unavailable, THEN THE Node_Server SHALL respond with HTTP status 502 and an error message.

### Requirement 2: Python microservice wraps the engine

**User Story:** As a developer, I want a Python microservice that wraps the engine, so that cost and price computation runs in Python behind a clean HTTP boundary.

#### Acceptance Criteria

1. THE Python_Service SHALL expose an HTTP operation that accepts a House_Profile and a Period and returns a Prediction_Result.
2. THE Python_Service SHALL expose an HTTP operation that accepts a House_Profile and a Period and returns a Savings_Result.
3. THE Python_Service SHALL expose an HTTP operation that returns per-month price values for a requested metric and a requested month count that is an integer in the range 1 to 60.
4. THE Python_Service SHALL expose an HTTP operation that returns the list of configured regions with each region's current price value as a number greater than or equal to 0.
5. WHEN the Python_Service receives a prediction request, THE Python_Service SHALL invoke `Predictor.predict_annual_cost(house, period)` and respond with HTTP status 200 and a JSON body containing a non-negative `cost_usd` and `missing_intervals` as ISO-8601 date strings.
6. WHEN the Python_Service receives a savings request, THE Python_Service SHALL invoke `Predictor.compare_savings(house, period)` and respond with HTTP status 200 and a JSON body containing `conventional_cost`, `heat_pump_cost`, and `savings` each rounded to two decimal places.
7. THE Python_Service SHALL serialize each response as JSON with an `application/json` content type.
8. IF the Engine raises a missing-field error or an invalid-period error, THEN THE Python_Service SHALL respond with HTTP status 400 and a JSON error message.
9. IF the Engine raises an unknown-region error or a missing-region-key error, THEN THE Python_Service SHALL respond with HTTP status 400 and a JSON error message.
10. IF the Python_Service receives a price request for an unsupported metric, THEN THE Python_Service SHALL respond with HTTP status 400 and a JSON error message.

### Requirement 3: Home form input mapped to engine request

**User Story:** As a homeowner, I want my home form to produce a real prediction, so that I see a cost forecast computed from my inputs rather than mock data.

#### Acceptance Criteria

1. WHEN the Frontend submits the home form with a non-empty location to the Predict_Endpoint, THE Node_Server SHALL set the House_Profile `region_key` to `MA`.
2. IF the home form is submitted with an empty or missing location, THEN THE Node_Server SHALL reject the request without calling the Python_Service and return a response indicating that location is required, preserving no partial House_Profile.
3. WHEN the Node_Server maps the home form, THE Node_Server SHALL derive a `heated_area_sqft` value that is a positive number greater than 0 and no greater than 100000, computed from the submitted home type and household size.
4. IF the home form is submitted without a home type or without a household size, THEN THE Node_Server SHALL set `heated_area_sqft` to a default positive value within the 0-exclusive to 100000-inclusive range.
5. WHEN a required House_Profile field among `setpoint_f`, `conventional_fuel`, and `system_efficiency` is not supplied by the home form, THE Node_Server SHALL apply a default such that `setpoint_f` is a positive Fahrenheit temperature, `conventional_fuel` is one of the fuels defined for region `MA`, and `system_efficiency` is greater than 0 and no greater than 1.
6. THE Node_Server SHALL send the Python_Service a House_Profile whose fields `region_key`, `heated_area_sqft`, `setpoint_f`, `conventional_fuel`, and `system_efficiency` are each present and non-empty.
7. THE Node_Server SHALL send the Python_Service a Period whose `end` is exactly 12 consecutive months after `start` and whose `end` is later than `start`.
8. WHEN the Python_Service returns a Prediction_Result with a non-negative `cost_usd`, THE Node_Server SHALL respond to the Frontend with a forecast payload containing the predicted cost from `cost_usd`.
9. IF the Python_Service returns an error or does not return a Prediction_Result, THEN THE Node_Server SHALL respond to the Frontend with a response indicating the forecast could not be generated and SHALL NOT return mock cost data.

### Requirement 4: Uploaded bill used as calibration reference

**User Story:** As a homeowner, I want my uploaded bill to improve the estimate when it contains a dollar figure, so that the forecast reflects my actual costs.

#### Acceptance Criteria

1. WHERE the home form submission includes a bill amount that is a number greater than 0 and within the range 0.01 to 999,999.99 (United States dollars), THE Node_Server SHALL treat that amount as the calibration reference actual and SHALL include it in the prediction request sent to the Python_Service.
2. WHEN the Python_Service receives a prediction request that includes a calibration reference actual, THE Python_Service SHALL complete a Calibration run against that reference actual and SHALL compute the returned prediction using the calibrated parameters produced by that run.
3. WHERE the home form submission includes no bill amount, THE Node_Server SHALL send the prediction request without a calibration reference actual.
4. IF a supplied bill amount is not a number, is less than or equal to 0, or falls outside the range 0.01 to 999,999.99, THEN THE Node_Server SHALL send the prediction request without a calibration reference actual and SHALL NOT reject the submission for that reason.
5. IF the Python_Service calibration run fails to complete because the reference actual is rejected or the reference data cannot be obtained within the 30-second provider deadline, THEN THE Python_Service SHALL return a prediction computed without calibration and SHALL include an indication that calibration was not applied.
6. WHEN the Python_Service returns a prediction computed using calibrated parameters, THE Node_Server SHALL return a response to the home form that includes an indication that the uploaded bill was used as the calibration reference.

### Requirement 5: Gemini-backed AI action plan

**User Story:** As a homeowner, I want an AI action plan after my forecast, so that I get tailored advice on lowering my bill.

#### Acceptance Criteria

1. WHEN the Frontend calls the Recommendations_Endpoint with a prediction that includes a predicted cost, THE Node_Server SHALL invoke the Gemini_Client to generate the Action_Plan.
2. IF the Recommendations_Endpoint is called with a request that omits the prediction or the predicted cost, THEN THE Node_Server SHALL reject the request without invoking the Gemini_Client and respond with an error message indicating the required prediction data is missing.
3. THE Node_Server SHALL include the predicted cost from the prediction in the prompt sent to the Gemini_Client.
4. WHEN the Gemini_Client returns a response within 30 seconds, THE Node_Server SHALL respond to the Frontend with an Action_Plan containing between 1 and 10 items, where each item has a non-empty title of at most 120 characters and a non-empty body of at most 2000 characters.
5. IF the Gemini_Client returns a response that contains no items or an item missing a title or body, THEN THE Node_Server SHALL respond with an error message indicating that a valid Action_Plan could not be generated.
6. IF the Gemini_Client call fails or does not return a response within 30 seconds, THEN THE Node_Server SHALL respond with HTTP status 502 and an error message that excludes the Gemini API key.

### Requirement 6: Price tracker backed by the microservice

**User Story:** As a homeowner, I want the price tracker to show real price trends and a regional comparison, so that I can monitor energy prices.

#### Acceptance Criteria

1. WHEN the Frontend calls the Prices_Endpoint with a supported `metric` value and a `months` value in the range 1 to 12 inclusive, THE Node_Server SHALL respond with HTTP status 200 and a price series obtained from the Python_Service for the requested metric, containing exactly `months` ordered monthly points ending with the most recent month.
2. WHEN the Node_Server returns a Prices_Endpoint price series, THE Node_Server SHALL include a unit label string for the requested metric and, for each returned month, a point containing a non-empty month label string and a numeric value greater than or equal to 0.
3. WHEN the Frontend calls the Regions_Endpoint, THE Node_Server SHALL respond with HTTP status 200 and the list of configured regions obtained from the Python_Service, where each list entry contains the region name string and the region's current numeric price value greater than or equal to 0.
4. IF the Prices_Endpoint receives a `metric` value that the Python_Service does not support, THEN THE Node_Server SHALL respond with HTTP status 400 and an error message indicating the metric is unsupported, without returning a price series.
5. IF the Prices_Endpoint receives a missing `months` value or a `months` value outside the range 1 to 12 inclusive, THEN THE Node_Server SHALL respond with HTTP status 400 and an error message indicating the months value is invalid, without returning a price series.
6. IF the Node_Server cannot obtain data from the Python_Service while serving the Prices_Endpoint or the Regions_Endpoint, THEN THE Node_Server SHALL respond with HTTP status 502 and an error message indicating the data source is unavailable.

### Requirement 7: Node-to-Python HTTP communication contract

**User Story:** As a developer, I want the Node and Python processes to communicate over a defined HTTP contract, so that the two language boundaries stay clean and debuggable.

#### Acceptance Criteria

1. WHEN the Node_Server needs a prediction, a savings comparison, or price data, THE Node_Server SHALL call the Python_Service over HTTP with a request timeout of 30 seconds.
2. THE Node_Server SHALL send each Python_Service request body as JSON with an `application/json` content type header.
3. WHEN the Python_Service returns a success HTTP status with a parseable JSON body, THE Node_Server SHALL parse that body before responding to the Frontend.
4. IF the Python_Service returns a success HTTP status with a body that cannot be parsed as JSON, THEN THE Node_Server SHALL respond to the Frontend with HTTP status 502 and a JSON error body, without forwarding the unparsed body.
5. IF the Python_Service responds with a non-success HTTP status, THEN THE Node_Server SHALL respond to the Frontend with HTTP status 502 and a JSON error body.
6. IF the Python_Service is unreachable or does not respond within the 30-second timeout, THEN THE Node_Server SHALL respond to the Frontend with HTTP status 502 and a JSON error body.

### Requirement 8: Live providers with placeholder fallback

**User Story:** As a developer, I want live data with a placeholder fallback, so that the system keeps working when a live source is down or a key is missing.

#### Acceptance Criteria

1. THE Python_Service SHALL use the Live_Weather_Provider and the Live_Pricing_Provider as the default data sources.
2. IF the EIA_API_KEY is missing, empty, or contains only whitespace, THEN THE Python_Service SHALL use a Placeholder_Provider for pricing instead of the Live_Pricing_Provider while keeping weather on the Live_Weather_Provider.
3. IF a Live_Weather_Provider call raises an error or exceeds the 30-second provider deadline, THEN THE Python_Service SHALL use a Placeholder_Provider for weather only and complete the request.
4. IF a Live_Pricing_Provider call raises an error or exceeds the 30-second provider deadline, THEN THE Python_Service SHALL use a Placeholder_Provider for pricing only and complete the request.
5. WHEN the Python_Service falls back to a Placeholder_Provider, THE Python_Service SHALL return a Prediction_Result, a Savings_Result, or price data whose JSON field names and nesting structure are identical to the live path.
6. WHEN the Python_Service uses a Placeholder_Provider for any source, THE Python_Service SHALL include in the response an indication that a fallback occurred and which source it applied to.

### Requirement 9: Graceful error handling across the chain

**User Story:** As a homeowner, I want clear errors instead of silent failures, so that I know when a forecast cannot be produced.

#### Acceptance Criteria

1. IF the Predict_Endpoint receives a request missing a required home form field, THEN THE Node_Server SHALL respond with HTTP status 400 within 2 seconds and a JSON body containing an error message that names each missing field.
2. IF the Engine raises a validation error for a missing House_Profile field or an invalid Period, THEN THE Python_Service SHALL respond with HTTP status 400 and a JSON body containing an error message that identifies the invalid or missing field and the reason for rejection.
3. IF the Engine raises an unknown-region error, THEN THE Python_Service SHALL respond with HTTP status 400 and a JSON body containing an error message that names the unrecognized region value.
4. WHEN the Node_Server responds to the Frontend with an error, THE Node_Server SHALL send a JSON body containing a non-empty error message string describing the failure cause.
5. WHEN the Frontend receives an error response from the Node_Server, THE Frontend SHALL display the error message text to the user within 1 second of receiving the response and SHALL NOT display a forecast result.
6. IF the Node_Server receives no response from the Python_Service within 10 seconds, or receives an HTTP status of 500 or greater, THEN THE Node_Server SHALL respond to the Frontend with HTTP status 502 and a JSON body containing an error message indicating the forecast service is unavailable.
