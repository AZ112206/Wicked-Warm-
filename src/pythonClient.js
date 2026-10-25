// Thin HTTP client to the Python_Service.
//
// Exposes predict / savings / prices / regions. Every call goes through a
// single `request()` helper that enforces the timeout contract and the
// status/parse guards described in the full-stack-integration design
// ("pythonClient.js contract behavior", Req 7.1-7.6, 9.6).
//
// Timeout contract:
//   - 30s socket timeout bounds the underlying connection (Req 7.1).
//   - A 10s watchdog is the effective forecast-service deadline (Req 9.6);
//     it fires first and aborts the in-flight request via AbortController.
//
// Error tagging (callers — the route handlers in task 4.2/4.3 — map `.status`):
//   - abort / network-unreachable  -> ServiceUnavailable (.status 502)
//   - 2xx with unparseable body     -> BadUpstreamBody   (.status 502),
//                                      raw body is never forwarded
//   - non-success >= 500            -> UpstreamError      (.status 502)
//   - upstream 400                  -> UpstreamError      (.status 400),
//                                      carrying the upstream `error` message

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

// The 10s watchdog is the effective deadline; the 30s value bounds the socket.
export const WATCHDOG_MS = 10_000;
export const SOCKET_TIMEOUT_MS = 30_000;

// Tag constants, exported so tests can assert on them without string literals.
export const ErrorKind = Object.freeze({
  SERVICE_UNAVAILABLE: "ServiceUnavailable",
  BAD_UPSTREAM_BODY: "BadUpstreamBody",
  UPSTREAM_ERROR: "UpstreamError",
});

function baseUrl() {
  // Read at call time so tests / deployments can set it dynamically.
  const value = process.env.PYTHON_SERVICE_URL;
  if (typeof value === "string" && value.trim() !== "") {
    return value.trim().replace(/\/+$/, "");
  }
  return DEFAULT_BASE_URL;
}

// Build a tagged Error the route handler can map straight to an HTTP status.
function taggedError(kind, message, status) {
  const err = new Error(message);
  err.kind = kind;
  err.code = kind; // alias; some callers key off `.code`
  err.status = status;
  return err;
}

function serviceUnavailable(message) {
  return taggedError(
    ErrorKind.SERVICE_UNAVAILABLE,
    message || "Forecast service is unavailable.",
    502
  );
}

function badUpstreamBody() {
  // Deliberately generic: never forward the raw upstream body (Req 7.4).
  return taggedError(
    ErrorKind.BAD_UPSTREAM_BODY,
    "Upstream service returned an unparseable response.",
    502
  );
}

// `status` is the HTTP status the caller should surface:
//   502 for upstream >= 500, 400 for an upstream validation passthrough.
function upstreamError(message, status) {
  return taggedError(
    ErrorKind.UPSTREAM_ERROR,
    message || "Upstream service error.",
    status
  );
}

// Build the URL. `query` values are expected to be already-encoded strings
// (callers encodeURIComponent them), so we assemble the query string by hand
// rather than through URLSearchParams to avoid double-encoding.
function buildUrl(path, query) {
  const base = new URL(path, baseUrl() + "/").toString();
  if (!query || typeof query !== "object") return base;
  const parts = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    parts.push(`${key}=${value}`);
  }
  if (parts.length === 0) return base;
  return `${base}?${parts.join("&")}`;
}

// Attempt to parse a response body as JSON; return undefined on failure so the
// caller decides whether an empty/invalid body is an error for that path.
async function tryParseJson(res) {
  try {
    const text = await res.text();
    if (text === "") return undefined;
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

async function request(method, path, { body, query } = {}) {
  const controller = new AbortController();
  // Watchdog is the effective deadline (Req 9.6): abort at 10s.
  const watchdog = setTimeout(() => controller.abort(), WATCHDOG_MS);

  const init = {
    method,
    signal: controller.signal,
    headers: {},
  };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json"; // Req 7.2
    init.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(buildUrl(path, query), init);
  } catch (err) {
    // AbortError (watchdog fired) or a network-unreachable failure. Either way
    // the forecast service is effectively unavailable (Req 7.6, 9.6).
    throw serviceUnavailable(
      err?.name === "AbortError"
        ? "Forecast service did not respond in time."
        : "Forecast service is unreachable."
    );
  } finally {
    clearTimeout(watchdog);
  }

  const parsed = await tryParseJson(res);

  if (res.ok) {
    // Success status but the body was empty or not valid JSON (Req 7.4).
    if (parsed === undefined) throw badUpstreamBody();
    return parsed;
  }

  // Non-success status. Prefer the upstream `error` field for the message.
  const upstreamMessage =
    parsed && typeof parsed.error === "string" && parsed.error !== ""
      ? parsed.error
      : undefined;

  if (res.status === 400) {
    // Validation passthrough: surface as 400 with the upstream message
    // (Req 2.8-2.10, 9.2, 9.3).
    throw upstreamError(upstreamMessage || "Invalid request.", 400);
  }

  // Everything else (including >= 500) collapses to a 502 (Req 7.5, 9.6).
  throw upstreamError(
    upstreamMessage || "Forecast service returned an error.",
    502
  );
}

export function predict(body) {
  return request("POST", "predict", { body });
}

export function savings(body) {
  return request("POST", "savings", { body });
}

export function prices(metric, months) {
  return request("GET", "prices", {
    query: {
      metric: encodeURIComponent(metric),
      months: encodeURIComponent(months),
    },
  });
}

export function regions() {
  return request("GET", "regions");
}
