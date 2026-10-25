// src/server.js
//
// The Node_Server: an Express application that serves the static frontend and
// exposes the four `/api` endpoints the browser calls. This module wires the
// middleware/route skeleton described in the full-stack-integration design
// ("New dependency and boot" + "Node_Server (src/server.js)").
//
// Boot contract (Req 1.6):
//   Importing ./config.js validates GEMINI_API_KEY and calls process.exit(1)
//   when it is missing — so simply importing it here terminates the process
//   before any socket is opened when the key is absent. Both secrets
//   (GEMINI_API_KEY, EIA_API_KEY) are read from the environment only; none are
//   ever written into source or echoed into a response.
//
// Middleware / route order (design "Node_Server (src/server.js)"):
//   1. express.json() body parser.
//   2. API routes under /api.
//   3. express.static for frontend/.
//   4. 404 handler -> { error: "Route not found" } (Req 1.4).
//   5. Central error handler -> { error } never including either secret
//      (Req 1.7, 9.4).
//
// The four /api routes are TEMPORARY 501 placeholders here; tasks 4.2 and 4.3
// replace the placeholder bodies with the real handlers. They are structured
// as named async handlers so those tasks can swap the body cleanly without
// touching the wiring.

import express from "express";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Importing config performs the GEMINI_API_KEY boot validation (Req 1.6).
// Keep this import even though the value is consumed by the route handlers in
// tasks 4.2/4.3 — the side effect (exit on missing key) is the point here.
import "./config.js";

// Pure form/response mapping (task 3.x) and the thin Python_Service client
// (task 3.1) feed the predict handler. The Gemini helper (unchanged) powers
// the recommendations handler.
import { mapFormToRequest, adaptForecast } from "./mapping.js";
import * as pythonClient from "./pythonClient.js";
import { ask } from "./ask.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = join(__dirname, "..", "frontend");

// Default listening port; overridable via the PORT environment variable.
const DEFAULT_PORT = 3000;

// ---------------------------------------------------------------------------
// Action-plan helpers (recommendations handler)
// ---------------------------------------------------------------------------

/** Action-plan validation bounds (design Property 8 / Req 5.4, 5.5). */
export const ACTION_PLAN_MIN_ITEMS = 1;
export const ACTION_PLAN_MAX_ITEMS = 10;
export const ACTION_PLAN_TITLE_MAX = 120;
export const ACTION_PLAN_BODY_MAX = 2000;

/**
 * Build the Gemini prompt for an energy-savings action plan. Embeds the
 * predicted monthly cost (Req 5.3) and, when present, light context from the
 * submitted form (location / home type). The prompt demands a strict,
 * parseable JSON array of {title, body} objects so the response maps cleanly
 * onto the shape the home renderer consumes.
 *
 * @param {number} amount predicted monthly cost in USD (finite)
 * @param {object|undefined} userInput optional browser form context
 * @returns {string}
 */
function buildActionPlanPrompt(amount, userInput) {
  const input = userInput && typeof userInput === "object" ? userInput : {};
  const contextLines = [];
  if (typeof input.location === "string" && input.location.trim() !== "") {
    contextLines.push(`Location: ${input.location.trim()}`);
  }
  if (typeof input.homeType === "string" && input.homeType.trim() !== "") {
    contextLines.push(`Home type: ${input.homeType.trim()}`);
  }
  if (
    typeof input.household === "number" &&
    Number.isFinite(input.household)
  ) {
    contextLines.push(`Household size: ${input.household}`);
  }
  const context =
    contextLines.length > 0 ? `\nHousehold context:\n${contextLines.join("\n")}\n` : "";

  const dollars = amount.toFixed(2);

  return [
    "You are an energy-efficiency advisor for a home in the northeastern United States.",
    `The household's predicted monthly energy cost is $${dollars}.`,
    context,
    "Produce a concrete, prioritized action plan of energy-saving steps tailored to that cost.",
    "",
    "Respond with ONLY a JSON array (no prose, no markdown, no code fences).",
    "The array must contain between 1 and 10 objects.",
    'Each object must have exactly two string fields: "title" and "body".',
    "The title is a short action label (at most 120 characters).",
    "The body explains the action and its expected impact (at most 2000 characters).",
    'Example: [{"title":"Seal drafts","body":"Weatherstrip doors and windows to cut heating loss."}]',
  ].join("\n");
}

/**
 * Parse raw Gemini text into an array of {title, body} items. Robust to code
 * fences and surrounding prose: strips ```json fences, then attempts to locate
 * and JSON.parse the first array in the text. Returns whatever structure was
 * parsed (possibly not an array / not well-formed) — validation is the
 * caller's responsibility via validateActionPlan.
 *
 * @param {unknown} text Gemini response text
 * @returns {unknown} parsed value (expected: array of {title, body})
 */
function parseActionPlan(text) {
  if (typeof text !== "string") return null;

  // Strip markdown code fences (```json ... ``` or ``` ... ```).
  let cleaned = text.trim();
  cleaned = cleaned.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();

  // First attempt: parse the whole cleaned string.
  const direct = tryJsonParse(cleaned);
  if (direct !== undefined) return direct;

  // Fallback: extract the first bracketed array substring and parse that.
  const start = cleaned.indexOf("[");
  const end = cleaned.lastIndexOf("]");
  if (start !== -1 && end > start) {
    const slice = cleaned.slice(start, end + 1);
    const parsed = tryJsonParse(slice);
    if (parsed !== undefined) return parsed;
  }

  return null;
}

/** JSON.parse that returns undefined instead of throwing. */
function tryJsonParse(s) {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}

/**
 * Validate a parsed action plan (design Property 8 / Req 5.4, 5.5).
 *
 * Accepts the plan iff it is an array of 1–10 items, each an object with a
 * non-empty string `title` of at most 120 characters and a non-empty string
 * `body` of at most 2000 characters. Any other structure is rejected.
 *
 * Exported so the property/unit tests in task 5.2 can import it directly.
 *
 * @param {unknown} items
 * @returns {boolean}
 */
export function validateActionPlan(items) {
  if (!Array.isArray(items)) return false;
  if (items.length < ACTION_PLAN_MIN_ITEMS || items.length > ACTION_PLAN_MAX_ITEMS) {
    return false;
  }
  return items.every((item) => {
    if (item === null || typeof item !== "object") return false;
    const { title, body } = item;
    if (typeof title !== "string" || typeof body !== "string") return false;
    if (title.trim() === "" || body.trim() === "") return false;
    if (title.length > ACTION_PLAN_TITLE_MAX) return false;
    if (body.length > ACTION_PLAN_BODY_MAX) return false;
    return true;
  });
}

// ---------------------------------------------------------------------------
// Price-query validation helper (prices handler)
// ---------------------------------------------------------------------------

/**
 * Metrics the Node layer supports. These mirror the Python_Service's supported
 * metrics (`electricity`, `gas`); any other value is rejected at the Node layer
 * with a 400 before the service is called (Req 6.4).
 */
export const SUPPORTED_PRICE_METRICS = Object.freeze(["electricity", "gas"]);

/** Inclusive bounds for the Node-layer `months` validation (Req 6.5). */
export const PRICE_MONTHS_MIN = 1;
export const PRICE_MONTHS_MAX = 12;
/** Applied only when `months` is entirely absent from the query (design note). */
export const PRICE_MONTHS_DEFAULT = 12;

/**
 * Validate the `GET /api/prices` query parameters at the Node layer
 * (Req 6.4, 6.5). This runs BEFORE any call to the Python_Service so an
 * unsupported metric or an invalid `months` never reaches the service and no
 * price series is returned.
 *
 * Rules:
 *   - `metric` must be one of SUPPORTED_PRICE_METRICS (else unsupported-metric
 *     error; Req 6.4).
 *   - `months` is optional: when absent entirely it defaults to 12 (the
 *     frontend always sends it; the default only covers a bare request). When
 *     present it must be an integer string in [1, 12]; anything else
 *     (non-integer, NaN, <1, >12, empty) is invalid (Req 6.5).
 *
 * The Node layer intentionally restricts `months` to 1–12 even though the
 * service accepts 1–60.
 *
 * Exported so the route-validation tests in task 5.1 can exercise it directly
 * without opening a socket.
 *
 * @param {unknown} metric raw req.query.metric
 * @param {unknown} months raw req.query.months
 * @returns {{ok: true, metric: string, months: number} | {ok: false, error: string}}
 */
export function validatePriceQuery(metric, months) {
  // Metric: must be a supported string. (Req 6.4)
  if (typeof metric !== "string" || !SUPPORTED_PRICE_METRICS.includes(metric)) {
    return {
      ok: false,
      error: `Unsupported metric. Supported metrics: ${SUPPORTED_PRICE_METRICS.join(", ")}.`,
    };
  }

  // Months: absent entirely → default; present → must be an integer in 1–12.
  // (Req 6.5)
  if (months === undefined || months === null) {
    return { ok: true, metric, months: PRICE_MONTHS_DEFAULT };
  }

  // A present value must be a clean integer string. Reject empty strings,
  // decimals, non-numeric text ("abc"), and anything that isn't a plain
  // integer token. express query values are strings, but tolerate a number too.
  const invalidMonths = {
    ok: false,
    error: "Invalid months value. Expected an integer from 1 to 12.",
  };
  const raw = typeof months === "string" ? months.trim() : months;
  if (raw === "" || (typeof raw !== "string" && typeof raw !== "number")) {
    return invalidMonths;
  }
  if (!/^-?\d+$/.test(String(raw))) {
    return invalidMonths;
  }
  const parsed = Number(raw);
  if (
    !Number.isInteger(parsed) ||
    parsed < PRICE_MONTHS_MIN ||
    parsed > PRICE_MONTHS_MAX
  ) {
    return invalidMonths;
  }

  return { ok: true, metric, months: parsed };
}

// ---------------------------------------------------------------------------
// Named route handlers
// ---------------------------------------------------------------------------

// Named handlers, one per route. Task 4.2 implemented predict/recommendations;
// task 4.3 implements prices/regions.

/**
 * POST /api/predict — browser forecast request (Req 1.2, 3.2, 3.8, 3.9, 9.1, 9.5).
 *
 * home.js posts the `userInput` object directly as the body. The handler:
 *   1. Validates a non-empty `location` BEFORE building any profile — an
 *      empty/missing location responds 400 naming the field and never maps a
 *      partial profile (Req 3.2, 9.1). This runs synchronously so it returns
 *      well within the ~2s budget.
 *   2. Maps the form to the engine request via mapFormToRequest (Req 3.x, 4.x).
 *   3. Calls pythonClient.predict — never fabricating cost data. Tagged client
 *      errors carry `.status` (400 passthrough or 502) and a safe `.message`.
 *   4. Adapts the engine result to the forecast payload (Req 3.8, 4.6). A
 *      missing/invalid cost_usd makes adaptForecast throw → mapped to 502.
 *
 * No secret is ever read here or placed in a response; error messages come
 * only from tagged client errors / adapter messages (Req 9.5).
 */
async function handlePredict(req, res, next) {
  try {
    const userInput = req.body && typeof req.body === "object" ? req.body : {};

    // 1. Validate non-empty location up front (no partial profile). (Req 3.2, 9.1)
    const location =
      typeof userInput.location === "string" ? userInput.location.trim() : "";
    if (location === "") {
      res.status(400).json({ error: "location is required" });
      return;
    }

    // 2. Map the form to the engine request (pure). (Req 3.x, 4.x)
    const request = mapFormToRequest(userInput);

    // 3. Call the Python_Service. Tagged errors propagate to the catch below
    //    where their .status (400/502) and .message drive the response.
    const predictResponse = await pythonClient.predict(request);

    // 4. Adapt the engine result to the frontend forecast payload. A missing or
    //    invalid cost_usd throws here (never mock data) → mapped to 502. (Req 3.9)
    let forecast;
    try {
      forecast = adaptForecast(predictResponse);
    } catch {
      res
        .status(502)
        .json({ error: "Forecast service returned an incomplete result." });
      return;
    }

    res.status(200).json(forecast);
  } catch (err) {
    // Tagged pythonClient errors carry a safe .status (400/502) and .message.
    // Routing through next(err) lets the central error handler emit {error}
    // without ever touching the environment. (Req 3.9, 9.5)
    next(err);
  }
}

/**
 * POST /api/recommendations — Gemini energy-savings action plan
 * (Req 1.2, 5.1–5.6, 9.5).
 *
 * home.js posts `{ prediction, userInput }`. The handler:
 *   1. Requires `prediction.amount` to be a finite number → else 400 (Req 5.2).
 *   2. Builds a prompt embedding the predicted cost and light context, asking
 *      Gemini for a strict JSON array of {title, body} items (Req 5.3).
 *   3. Calls ask(prompt); any throw/timeout → 502 with a safe message that
 *      never includes the key (Req 5.6, 1.7).
 *   4. Parses + validates the text into 1–10 {title, body} items (title ≤120,
 *      body ≤2000) → else an error that a valid plan could not be generated
 *      (Req 5.4, 5.5). On success responds 200 with the array (Req 5.1).
 */
async function handleRecommendations(req, res, next) {
  try {
    const body = req.body && typeof req.body === "object" ? req.body : {};
    const prediction =
      body.prediction && typeof body.prediction === "object" ? body.prediction : {};

    // 1. Require a numeric predicted cost. (Req 5.2)
    const amount = prediction.amount;
    if (typeof amount !== "number" || !Number.isFinite(amount)) {
      res.status(400).json({ error: "prediction.amount is required" });
      return;
    }

    // 2. Build the prompt (Req 5.3).
    const prompt = buildActionPlanPrompt(amount, body.userInput);

    // 3. Call Gemini. Any failure/timeout → 502 with a safe message; the raw
    //    SDK error (which could echo configuration detail) is never forwarded,
    //    and no secret is included. (Req 5.6, 1.7)
    let text;
    try {
      text = await ask(prompt);
    } catch {
      res
        .status(502)
        .json({ error: "The action-plan service is currently unavailable." });
      return;
    }

    // 4. Parse + validate into 1–10 well-formed items. (Req 5.4, 5.5)
    const items = parseActionPlan(text);
    if (!validateActionPlan(items)) {
      res
        .status(502)
        .json({ error: "A valid action plan could not be generated." });
      return;
    }

    res.status(200).json(items);
  } catch (err) {
    next(err);
  }
}

/**
 * GET /api/prices — price series for a supported metric
 * (Req 1.2, 1.8, 6.1, 6.2, 6.4, 6.5, 6.6).
 *
 * price-tracker.js calls `GET /api/prices?metric=&months=`. The handler:
 *   1. Validates `metric` is supported and `months` is an integer in 1–12 via
 *      validatePriceQuery, BEFORE any service call. An unsupported metric or an
 *      invalid months responds 400 and returns no price series (Req 6.4, 6.5).
 *   2. Calls pythonClient.prices(metric, months). The service returns
 *      `{metric, unit, points, fallbacks}`; the frontend shape is
 *      `{metric, unit, points}`, so `fallbacks` is dropped (Req 6.1, 6.2).
 *   3. A Python_Service that is unavailable/unreachable (or returns ≥ 500 /
 *      an unparseable body) surfaces as a tagged client error → mapped to 502
 *      by the central error handler via next(err) (Req 6.6, 1.8).
 *
 * No secret is read or placed in any response here.
 */
async function handlePrices(req, res, next) {
  try {
    const query = req.query && typeof req.query === "object" ? req.query : {};

    // 1. Validate metric + months at the Node layer (no service call on
    //    failure, no series returned). (Req 6.4, 6.5)
    const validation = validatePriceQuery(query.metric, query.months);
    if (!validation.ok) {
      res.status(400).json({ error: validation.error });
      return;
    }

    // 2. Call the Python_Service. Tagged errors propagate to the catch below
    //    where their .status (502 unavailable) drives the response. (Req 6.6)
    const response = await pythonClient.prices(
      validation.metric,
      validation.months
    );

    // 3. Shape the frontend response: {metric, unit, points}. Prefer the
    //    service's echoed metric but fall back to the validated one so the
    //    response always carries a metric. (Req 6.1, 6.2)
    const payload = {
      metric:
        response && typeof response.metric === "string"
          ? response.metric
          : validation.metric,
      unit: response ? response.unit : undefined,
      points: response ? response.points : undefined,
    };

    res.status(200).json(payload);
  } catch (err) {
    // Tagged pythonClient errors carry a safe .status (502 for unavailable)
    // and .message; the central error handler emits {error} without touching
    // the environment. (Req 6.6, 9.5)
    next(err);
  }
}

/**
 * GET /api/prices/regions — regional price table (Req 1.2, 6.3, 6.6).
 *
 * price-tracker.js calls `GET /api/prices/regions`. The handler:
 *   1. Calls pythonClient.regions(). The service returns `{regions, fallbacks}`;
 *      the frontend shape is the bare array `[{region, rate, change}]`, so the
 *      handler extracts `response.regions` (Req 6.3).
 *   2. A Python_Service that is unavailable surfaces as a tagged client error
 *      → mapped to 502 via next(err) (Req 6.6).
 *
 * No secret is read or placed in any response here.
 */
async function handlePriceRegions(_req, res, next) {
  try {
    // 1. Call the Python_Service. (Req 6.3)
    const response = await pythonClient.regions();

    // Extract the array the frontend renders; guard against a missing/invalid
    // `regions` field by falling back to an empty array (the service contract
    // guarantees an array, but never emit `undefined`).
    const regions =
      response && Array.isArray(response.regions) ? response.regions : [];

    res.status(200).json(regions);
  } catch (err) {
    // Tagged pythonClient errors (502 for unavailable) map via the central
    // error handler. (Req 6.6)
    next(err);
  }
}

// ---------------------------------------------------------------------------
// Application factory
// ---------------------------------------------------------------------------

/**
 * Build the Express application with the full middleware/route order. Exported
 * so tests (task 5.x) can exercise routes without opening a socket.
 * @returns {import("express").Express}
 */
export function createApp() {
  const app = express();

  // 1. JSON body parser.
  app.use(express.json());

  // 2. API routes under /api (temporary 501 placeholders).
  app.post("/api/predict", handlePredict);
  app.post("/api/recommendations", handleRecommendations);
  app.get("/api/prices", handlePrices);
  app.get("/api/prices/regions", handlePriceRegions);

  // 3. Static middleware for frontend/ so GET /home/index.html,
  //    /price-tracker/price-tracker.html, etc. resolve to files (Req 1.1).
  app.use(express.static(FRONTEND_DIR));

  // 4. Catch-all 404 handler (Req 1.4).
  app.use((_req, res) => {
    res.status(404).json({ error: "Route not found" });
  });

  // 5. Central error handler (Req 1.7, 9.4). Emits JSON { error } and never
  //    includes either secret. The message is taken from the error's own
  //    message (set by our handlers / tagged client errors), never from the
  //    environment, so a secret cannot leak through here. Express requires the
  //    four-argument signature to recognize this as an error handler.
  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    const status = Number.isInteger(err?.status) ? err.status : 500;
    const message =
      typeof err?.message === "string" && err.message !== ""
        ? err.message
        : "Internal server error.";
    res.status(status).json({ error: message });
  });

  return app;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

/**
 * Resolve the listening port from PORT, falling back to the default when the
 * variable is unset or not a positive integer.
 * @returns {number}
 */
export function resolvePort() {
  const raw = process.env.PORT;
  if (typeof raw === "string" && raw.trim() !== "") {
    const parsed = Number(raw);
    if (Number.isInteger(parsed) && parsed > 0) return parsed;
  }
  return DEFAULT_PORT;
}

// Start listening only when run directly (node src/server.js), so importing
// this module in a test does not bind a socket.
const isMain =
  process.argv[1] &&
  import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/"));

if (isMain) {
  const app = createApp();
  const port = resolvePort();
  app.listen(port, () => {
    console.log(`Wicked Warm server listening on http://127.0.0.1:${port}`);
  });
}
