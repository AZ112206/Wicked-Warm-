// src/mapping.js
//
// Pure mapping functions bridging the browser home-form `userInput` object and
// the Python_Service engine request shapes (HouseProfile + Period). These are
// side-effect-free so they can be unit- and property-tested in isolation
// (see full-stack-integration tasks 3.4–3.6).
//
// This module intentionally exports small, composable helpers
// (`deriveHeatedAreaSqft`, `derivePeriod`, `normalizeBillAmount`) alongside the
// top-level `mapFormToRequest`. Task 3.3 appends the engine-result → forecast
// payload adapter to this same file.
//
// Design reference: full-stack-integration design.md
//   "Home-form → HouseProfile + Period mapping".

// ---------------------------------------------------------------------------
// Constants (engine defaults for the web session)
// ---------------------------------------------------------------------------

/** The only region configured in the engine's registry today (Req 3.1). */
export const DEFAULT_REGION_KEY = "MA";

/** Default HouseProfile field values when the form does not supply them (Req 3.5). */
export const HOUSE_DEFAULTS = Object.freeze({
  house_id: "web-session",
  // Positive per-(sqft-degree-hour) heat-loss seed. The engine computes
  // UA_effective = ua_coefficient * heated_area_sqft * demand_scale, so a 0.0
  // seed would zero out demand and every forecast. This value mirrors the
  // engine's reference example (energy_cost_predictor/example.py: ~0.00004 for
  // a 2000 sqft house); calibration tunes demand_scale from here when a bill is
  // supplied.
  ua_coefficient: 0.00004,
  setpoint_f: 68, // positive Fahrenheit (Req 3.5)
  conventional_fuel: "natural_gas", // a fuel defined for region MA (Req 3.5)
  system_efficiency: 0.9, // within (0, 1] (Req 3.5)
});

/** heated_area_sqft clamp bounds: strictly > 0 and ≤ 100000 (Req 3.3). */
export const HEATED_AREA_MIN = 1;
export const HEATED_AREA_MAX = 100000;

/** Default heated area when homeType or household is missing (Req 3.4). */
export const DEFAULT_HEATED_AREA_SQFT = 1500;

/** Base square footage by home type; assumes a 2-occupant household. */
const BASE_BY_HOME_TYPE = Object.freeze({
  apartment: 900,
  condo: 1100,
  townhouse: 1500,
  single_family: 2000,
  other: 1500,
});

const BASE_FALLBACK = 1500; // base when homeType is present but unrecognized
const PER_PERSON_SQFT = 250;
const ASSUMED_OCCUPANTS = 2;

/** Inclusive bill-amount range accepted as a calibration reference (Req 4.1, 4.4). */
export const BILL_AMOUNT_MIN = 0.01;
export const BILL_AMOUNT_MAX = 999999.99;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Derive `heated_area_sqft` from the submitted home type and household size.
 *
 * Formula (design "Home-form → HouseProfile + Period mapping"):
 *   base = BASE_BY_HOME_TYPE[homeType] (or 1500 when homeType is unrecognized)
 *   raw  = base + 250 * max(0, household - 2)
 *   result = min(100000, max(1, round(raw)))
 *
 * When either `homeType` or `household` is missing (or household is not a
 * usable number), the value defaults to 1500 (Req 3.4). The returned value is
 * always strictly greater than 0 and no greater than 100000 (Req 3.3).
 *
 * @param {string|null|undefined} homeType
 * @param {number|string|null|undefined} household
 * @returns {number} heated area in square feet, in (0, 100000]
 */
export function deriveHeatedAreaSqft(homeType, household) {
  const hasHomeType = homeType !== undefined && homeType !== null && String(homeType).trim() !== "";
  const householdNum = typeof household === "number" ? household : Number(household);
  const hasHousehold =
    household !== undefined &&
    household !== null &&
    String(household).trim() !== "" &&
    Number.isFinite(householdNum);

  // Missing either input → default (Req 3.4).
  if (!hasHomeType || !hasHousehold) {
    return DEFAULT_HEATED_AREA_SQFT;
  }

  const base = Object.prototype.hasOwnProperty.call(BASE_BY_HOME_TYPE, homeType)
    ? BASE_BY_HOME_TYPE[homeType]
    : BASE_FALLBACK;

  const raw = base + PER_PERSON_SQFT * Math.max(0, householdNum - ASSUMED_OCCUPANTS);

  // Clamp into (0, 100000] (Req 3.3).
  return Math.min(HEATED_AREA_MAX, Math.max(HEATED_AREA_MIN, Math.round(raw)));
}

/**
 * Format a Date's UTC parts as an ISO-8601 "YYYY-MM-DD" string.
 * Uses UTC date parts to avoid timezone drift.
 * @param {number} year full year
 * @param {number} monthIndex 0-based month (0 = January)
 * @param {number} day day of month
 * @returns {string}
 */
function isoDate(year, monthIndex, day) {
  const mm = String(monthIndex + 1).padStart(2, "0");
  const dd = String(day).padStart(2, "0");
  return `${year}-${mm}-${dd}`;
}

/**
 * Derive a 12-consecutive-month Period ending with the current month.
 *
 *   start = first day of the month 11 months before the current month
 *   end   = last day of the current month
 *
 * This yields exactly 12 consecutive months with `end` strictly later than
 * `start` (Req 3.7). All arithmetic uses UTC date parts so the result does not
 * drift across timezones.
 *
 * @param {Date} [now=new Date()] the reference "current" moment
 * @returns {{start: string, end: string}} ISO-8601 "YYYY-MM-DD" bounds
 */
export function derivePeriod(now = new Date()) {
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth(); // 0-based

  // start: first day of the month 11 months before the current month.
  // Date UTC normalizes a negative month index back across the year boundary.
  const startDate = new Date(Date.UTC(year, month - 11, 1));
  const start = isoDate(startDate.getUTCFullYear(), startDate.getUTCMonth(), 1);

  // end: last day of the current month = day 0 of the following month.
  const endDate = new Date(Date.UTC(year, month + 1, 0));
  const end = isoDate(endDate.getUTCFullYear(), endDate.getUTCMonth(), endDate.getUTCDate());

  return { start, end };
}

/**
 * Normalize a candidate bill amount to a calibration reference value, or null.
 *
 * Returns the number when it is finite and within the inclusive range
 * [0.01, 999999.99] (Req 4.1). Returns null for any non-numeric value or a
 * value outside that range (Req 4.3, 4.4) — the caller omits
 * `reference_actual_usd` in that case and never rejects the submission.
 *
 * @param {unknown} amount
 * @returns {number|null}
 */
export function normalizeBillAmount(amount) {
  if (typeof amount !== "number" || !Number.isFinite(amount)) {
    return null;
  }
  if (amount < BILL_AMOUNT_MIN || amount > BILL_AMOUNT_MAX) {
    return null;
  }
  return amount;
}

// ---------------------------------------------------------------------------
// Top-level form → engine request mapping
// ---------------------------------------------------------------------------

/**
 * Map the browser `userInput` object to a Python_Service prediction request.
 *
 * The route handler (task 4.2) validates a non-empty `location` BEFORE calling
 * this function, so `region_key` is always set to "MA" here (Req 3.1). The
 * bill amount is read from `userInput.billAmount` (a finite number the handler
 * parses from the uploaded bill); it is included as `reference_actual_usd` only
 * when it falls within [0.01, 999999.99] (Req 4.1, 4.3, 4.4).
 *
 * @param {object} userInput browser form object (location, household, homeType,
 *   income, billFileNames, billAmount?)
 * @param {Date} [now=new Date()] reference moment for the derived period
 * @returns {{house: object, period: {start: string, end: string}, reference_actual_usd?: number}}
 */
export function mapFormToRequest(userInput, now = new Date()) {
  const input = userInput || {};

  const house = {
    house_id: HOUSE_DEFAULTS.house_id,
    region_key: DEFAULT_REGION_KEY, // always "MA" — runs only after location validation (Req 3.1)
    heated_area_sqft: deriveHeatedAreaSqft(input.homeType, input.household),
    ua_coefficient: HOUSE_DEFAULTS.ua_coefficient,
    setpoint_f: HOUSE_DEFAULTS.setpoint_f,
    conventional_fuel: HOUSE_DEFAULTS.conventional_fuel,
    system_efficiency: HOUSE_DEFAULTS.system_efficiency,
  };

  const period = derivePeriod(now);

  const request = { house, period };

  const reference = normalizeBillAmount(input.billAmount);
  if (reference !== null) {
    request.reference_actual_usd = reference;
  }

  return request;
}

// ---------------------------------------------------------------------------
// Engine result → frontend forecast payload adapter (Task 3.3)
// ---------------------------------------------------------------------------
//
// Adapts a Python_Service /predict response ({cost_usd, missing_intervals,
// calibration_applied, fallbacks}) into the payload the home page renderer
// (frontend/home/home.js) consumes: {amount, lastMonth, currency, series}.
//
// Design reference: full-stack-integration design.md
//   "Engine result → frontend forecast payload".

/** Short month labels indexed by 0-based month (0 = Jan). */
const MONTH_LABELS = Object.freeze([
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]);

/**
 * Deterministic seasonal shape multipliers keyed by 0-based calendar month.
 * Heating-season months (deep winter) are relatively dearer and summer/shoulder
 * months softer — mirroring the Python_Service engine's _SEASONAL_MULTIPLIERS
 * idea (energy_cost_predictor/service.py). These are *relative* weights; the
 * series is rescaled by the average multiplier so the twelve monthly values
 * still sum to ~cost_usd.
 */
const SEASONAL_MULTIPLIERS = Object.freeze([
  1.08, // Jan — deep winter peak
  1.06, // Feb
  1.02, // Mar
  0.98, // Apr
  0.94, // May
  0.92, // Jun — summer trough
  0.92, // Jul
  0.93, // Aug
  0.95, // Sep
  0.99, // Oct
  1.03, // Nov — heating season ramp
  1.07, // Dec
]);

/** Average of the seasonal multipliers (used to renormalize the split). */
const SEASONAL_MULTIPLIER_AVG =
  SEASONAL_MULTIPLIERS.reduce((sum, m) => sum + m, 0) / SEASONAL_MULTIPLIERS.length;

/** Number of trailing monthly points shown in the forecast series. */
const FORECAST_SERIES_LENGTH = 6;

/** Round a number to 2 decimal places, returning a finite non-negative value. */
function round2(n) {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

/**
 * Adapt a Python_Service /predict response to the frontend forecast payload.
 *
 * The series is a seasonal split of the predicted annual cost: the annual
 * `cost_usd` is distributed across the twelve calendar months using
 * `SEASONAL_MULTIPLIERS` (winter heavier) renormalized by their average so the
 * twelve months still sum to ~`cost_usd`. The six trailing months ending at the
 * current month (from `now`, computed from UTC parts for testability) form the
 * series; its final point carries `forecast: true` ("recent actuals + forecast
 * month"). The headline `amount` is the plain monthly average (`cost_usd / 12`)
 * per the design, and `lastMonth` is the second-to-last series value the
 * renderer uses for its trend arrow.
 *
 * Never fabricates cost data: if `predictResponse` is missing or `cost_usd` is
 * not a finite non-negative number, this throws — the route handler maps the
 * error (Req 3.9).
 *
 * @param {{cost_usd: number, missing_intervals?: unknown, calibration_applied?: boolean, fallbacks?: unknown}} predictResponse
 * @param {Date} [now=new Date()] reference "current" moment (UTC parts used)
 * @returns {{amount: number, lastMonth: number, currency: "USD", series: Array<{label: string, value: number, forecast?: boolean}>, calibrated?: boolean}}
 * @throws {Error} when the response is missing or carries an invalid cost
 */
export function adaptForecast(predictResponse, now = new Date()) {
  if (predictResponse === null || typeof predictResponse !== "object") {
    throw new Error("adaptForecast: missing predict response");
  }

  const cost = predictResponse.cost_usd;
  if (typeof cost !== "number" || !Number.isFinite(cost) || cost < 0) {
    throw new Error(
      "adaptForecast: predict response has no finite non-negative cost_usd"
    );
  }

  // Monthly average (headline figure) and the seasonal per-month base.
  const monthlyBase = cost / 12;

  // Seasonal value for a given 0-based month: base rescaled by the month's
  // relative multiplier over the average, so the twelve months sum to ~cost.
  const seasonalValue = (monthIndex) =>
    round2(
      Math.max(0, (monthlyBase * SEASONAL_MULTIPLIERS[monthIndex]) / SEASONAL_MULTIPLIER_AVG)
    );

  const currentMonth = now.getUTCMonth(); // 0-based

  // Six trailing months ending at the current month.
  const series = [];
  for (let offset = FORECAST_SERIES_LENGTH - 1; offset >= 0; offset -= 1) {
    const monthIndex = ((currentMonth - offset) % 12 + 12) % 12;
    const point = { label: MONTH_LABELS[monthIndex], value: seasonalValue(monthIndex) };
    if (offset === 0) {
      point.forecast = true; // final point is the forecast month
    }
    series.push(point);
  }

  // lastMonth = second-to-last series value (renderer's trend-arrow baseline).
  const lastMonth = series[series.length - 2].value;

  const payload = {
    amount: round2(monthlyBase),
    lastMonth,
    currency: "USD",
    series,
  };

  if (predictResponse.calibration_applied === true) {
    payload.calibrated = true; // uploaded bill was used (Req 4.6)
  }

  return payload;
}
