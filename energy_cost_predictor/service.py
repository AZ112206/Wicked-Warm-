"""HTTP microservice wrapping the Energy Cost Predictor engine (Req 2, 8).

This module is the Python_Service from the full-stack-integration design. It
puts a thin, standard-library ``http.server`` boundary in front of the
``energy_cost_predictor`` engine and exposes four JSON operations consumed by
the Node_Server over localhost HTTP:

====================  ======  ===========================================
Operation             HTTP    Response (200)
====================  ======  ===========================================
Predict               POST    ``{cost_usd, missing_intervals,
                      /predict calibration_applied, fallbacks}``
Savings               POST    ``{conventional_cost, heat_pump_cost,
                      /savings savings, fallbacks}``
Prices                GET     ``{metric, unit, points, fallbacks}``
                      /prices
Regions               GET     ``{regions: [...], fallbacks}``
                      /regions
====================  ======  ===========================================

Every response is JSON with an ``application/json`` content type (Req 2.7).

Provider construction and per-source fallback (Req 8)
-----------------------------------------------------
The service builds a fresh :class:`~energy_cost_predictor.predictor.Predictor`
per request via :func:`build_predictor`. The default data sources are the live
providers — :class:`OpenMeteoWeatherProvider` (no key) and
:class:`EIAPricingProvider` (reads ``EIA_API_KEY`` from the environment only).

Each live provider is wrapped in a *guarding provider* that delegates to the
live provider and, on any exception (including the adapter's
:class:`ProviderTimeoutError` / :class:`ProviderDataError`), delegates to the
matching placeholder and flips the corresponding ``fallbacks`` flag. Because the
guarding provider returns the same ``WeatherSeries`` / ``PriceTable`` dataclasses
on both paths, the engine and the serialized JSON are shape-identical whether a
source is live or has fallen back (Req 8.5). If ``EIA_API_KEY`` is missing,
empty, or whitespace-only, pricing starts on the placeholder with
``fallbacks.pricing = True`` (Req 8.2).

Runnable as ``python -m energy_cost_predictor.service`` on a configurable port
(default 8000); set ``PORT`` in the environment or pass ``--port`` / a positional
port argument.

This module depends only on the Python standard library plus the engine package,
so it introduces no new third-party dependency.

The per-operation request handling (predict with optional calibration, savings,
price-series synthesis, regions synthesis) and the engine-exception -> HTTP
status mapping are layered on top of this skeleton in subsequent tasks; the
dispatch structure here is built to accommodate them.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from energy_cost_predictor.calibration import Calibration
from energy_cost_predictor.errors import (
    InvalidActualCostError,
    ProviderTimeoutError,
)
from energy_cost_predictor.live_pricing_provider import EIAPricingProvider
from energy_cost_predictor.live_weather_provider import OpenMeteoWeatherProvider
from energy_cost_predictor.models import HouseProfile, PriceTable, WeatherSeries
from energy_cost_predictor.placeholder_providers import (
    PlaceholderPricingProvider,
    PlaceholderReferenceActualProvider,
    PlaceholderWeatherProvider,
)
from energy_cost_predictor.predictor import Period, Predictor
from energy_cost_predictor.provider_adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    PricingProviderAdapter,
    WeatherProviderAdapter,
)

__all__ = [
    "DEFAULT_PORT",
    "Fallbacks",
    "GuardingWeatherProvider",
    "GuardingPricingProvider",
    "build_predictor",
    "ServiceHandler",
    "make_server",
    "main",
]

#: Default port the service listens on when none is supplied (env ``PORT`` or
#: a command-line argument override it).
DEFAULT_PORT: int = 8000


# ---------------------------------------------------------------------------
# Per-source fallback tracking (Req 8.2, 8.3, 8.4, 8.6)
# ---------------------------------------------------------------------------


class Fallbacks:
    """Mutable record of which data sources fell back to a placeholder.

    A single instance is shared between the guarding providers built for one
    request and the response serializer, so the handler can report exactly which
    source(s) used a placeholder (Req 8.6). ``weather`` and ``pricing`` start
    ``False`` and are flipped to ``True`` the first time the matching source
    falls back.
    """

    def __init__(self, weather: bool = False, pricing: bool = False) -> None:
        self.weather = weather
        self.pricing = pricing

    def as_dict(self) -> Dict[str, bool]:
        """Return the JSON-serializable ``{weather, pricing}`` flag object."""
        return {"weather": self.weather, "pricing": self.pricing}


# ---------------------------------------------------------------------------
# Guarding providers: live-first with per-source placeholder fallback (Req 8)
# ---------------------------------------------------------------------------


class GuardingWeatherProvider:
    """Weather provider that falls back to a placeholder on any failure (Req 8.3).

    Delegates to the live provider through a
    :class:`~energy_cost_predictor.provider_adapter.WeatherProviderAdapter` (so
    the engine's 30-second deadline and required-field guards apply to the live
    call). If that raises for any reason — a network/parse error,
    :class:`ProviderTimeoutError`, or :class:`ProviderDataError` — it delegates
    to :class:`PlaceholderWeatherProvider` for weather only and flips
    ``fallbacks.weather`` (Req 8.3). The returned :class:`WeatherSeries` has the
    same shape on both paths (Req 8.5).

    Implements the ``WeatherProvider`` Protocol shape, so the ``Predictor`` can
    use it wherever a raw weather provider would appear.
    """

    def __init__(
        self,
        live: Any,
        placeholder: Any,
        fallbacks: Fallbacks,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._live_adapter = WeatherProviderAdapter(
            live,
            timeout_seconds=timeout_seconds,
            provider_name="OpenMeteoWeatherProvider",
        )
        self._placeholder = placeholder
        self._fallbacks = fallbacks

    def get_temperatures(
        self, region_key: str, start: date, end: date
    ) -> WeatherSeries:
        try:
            return self._live_adapter.get_temperatures(region_key, start, end)
        except Exception:
            # Any failure on the live source -> placeholder for weather only
            # (Req 8.3). Record that weather fell back (Req 8.6).
            self._fallbacks.weather = True
            return self._placeholder.get_temperatures(region_key, start, end)


class GuardingPricingProvider:
    """Pricing provider that falls back to a placeholder on any failure (Req 8.4).

    Delegates to the live provider through a
    :class:`~energy_cost_predictor.provider_adapter.PricingProviderAdapter`. If
    that raises for any reason — including :class:`ProviderTimeoutError` or
    :class:`ProviderDataError` — it delegates to
    :class:`PlaceholderPricingProvider` for pricing only and flips
    ``fallbacks.pricing`` (Req 8.4). The returned :class:`PriceTable` is
    shape-identical on both paths (Req 8.5).

    Implements the ``PricingProvider`` Protocol shape.
    """

    def __init__(
        self,
        live: Any,
        placeholder: Any,
        fallbacks: Fallbacks,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._live_adapter = PricingProviderAdapter(
            live,
            timeout_seconds=timeout_seconds,
            provider_name="EIAPricingProvider",
        )
        self._placeholder = placeholder
        self._fallbacks = fallbacks

    def get_prices(self, region_key: str) -> PriceTable:
        try:
            return self._live_adapter.get_prices(region_key)
        except Exception:
            # Any failure on the live source -> placeholder for pricing only
            # (Req 8.4). Record that pricing fell back (Req 8.6).
            self._fallbacks.pricing = True
            return self._placeholder.get_prices(region_key)


def _eia_api_key() -> Optional[str]:
    """Return the ``EIA_API_KEY`` from the environment, or ``None`` if unusable.

    Read from the environment only (Req 8.2, secrets stay server-side). A
    missing, empty, or whitespace-only value is treated as absent so pricing
    starts on the placeholder.
    """
    raw = os.environ.get("EIA_API_KEY")
    if raw is None or raw.strip() == "":
        return None
    return raw


def build_predictor(
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[Predictor, Fallbacks]:
    """Build a per-request :class:`Predictor` with per-source fallback (Req 8).

    Weather defaults to the live :class:`OpenMeteoWeatherProvider`; pricing
    defaults to the live :class:`EIAPricingProvider` (Req 8.1). Each live
    provider is wrapped in a guarding provider that falls back to the matching
    placeholder on any failure and records it on the shared :class:`Fallbacks`
    (Req 8.3, 8.4, 8.6).

    If ``EIA_API_KEY`` is missing/empty/whitespace, pricing starts on
    :class:`PlaceholderPricingProvider` with ``fallbacks.pricing = True`` and no
    live EIA call is attempted (Req 8.2).

    :returns: ``(predictor, fallbacks)`` — the predictor wired to the guarding
        providers, and the shared fallback record the caller attaches to the
        response.
    """
    fallbacks = Fallbacks()

    # Weather: live Open-Meteo (no key) guarded by the placeholder (Req 8.1, 8.3).
    weather_provider: Any = GuardingWeatherProvider(
        live=OpenMeteoWeatherProvider(),
        placeholder=PlaceholderWeatherProvider(),
        fallbacks=fallbacks,
        timeout_seconds=timeout_seconds,
    )

    # Pricing: live EIA guarded by the placeholder, unless the key is absent in
    # which case start on the placeholder (Req 8.1, 8.2, 8.4).
    placeholder_pricing = PlaceholderPricingProvider()
    api_key = _eia_api_key()
    if api_key is None:
        # No usable key: use the placeholder from the start and mark pricing as
        # fallen back without attempting a live call (Req 8.2).
        fallbacks.pricing = True
        pricing_provider: Any = placeholder_pricing
    else:
        pricing_provider = GuardingPricingProvider(
            live=EIAPricingProvider(api_key=api_key),
            placeholder=placeholder_pricing,
            fallbacks=fallbacks,
            timeout_seconds=timeout_seconds,
        )

    predictor = Predictor(
        weather_provider=weather_provider,
        pricing_provider=pricing_provider,
        timeout_seconds=timeout_seconds,
    )
    return predictor, fallbacks


# ---------------------------------------------------------------------------
# Request-body parsing (house + period)
# ---------------------------------------------------------------------------


def _parse_house(raw: Any) -> HouseProfile:
    """Build a :class:`HouseProfile` from the request body's ``house`` object.

    The incoming JSON object carries the ``HouseProfile`` fields by name. Absent
    fields are passed through as ``None`` so the engine's required-field
    validation (:meth:`Predictor._validate_house`) raises a named
    :class:`~energy_cost_predictor.errors.MissingFieldError` — the parser does
    not substitute defaults for required fields. ``house_id`` and
    ``ua_coefficient`` are engine seeds with harmless defaults when omitted.

    :raises ValueError: If ``house`` is present but not a JSON object.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("'house' must be a JSON object.")
    return HouseProfile(
        house_id=raw.get("house_id", "web-session"),
        region_key=raw.get("region_key"),
        heated_area_sqft=raw.get("heated_area_sqft"),
        ua_coefficient=raw.get("ua_coefficient", 0.0),
        setpoint_f=raw.get("setpoint_f"),
        conventional_fuel=raw.get("conventional_fuel"),
        system_efficiency=raw.get("system_efficiency"),
    )


def _parse_period(raw: Any) -> Period:
    """Build a :class:`Period` from the request body's ``period`` object.

    ``period.start`` and ``period.end`` are ISO-8601 date strings
    (``YYYY-MM-DD``); they are parsed with :meth:`datetime.date.fromisoformat`.

    :raises ValueError: If ``period`` is missing, not a JSON object, lacks
        ``start``/``end``, or either value is not a valid ISO-8601 date string.
    """
    if not isinstance(raw, dict):
        raise ValueError("'period' must be a JSON object with 'start' and 'end'.")
    start_raw = raw.get("start")
    end_raw = raw.get("end")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        raise ValueError("'period.start' and 'period.end' must be ISO-8601 date strings.")
    try:
        start = date.fromisoformat(start_raw)
        end = date.fromisoformat(end_raw)
    except ValueError:
        raise ValueError(
            "'period.start' and 'period.end' must be valid ISO-8601 dates (YYYY-MM-DD)."
        )
    return Period(start=start, end=end)


# ---------------------------------------------------------------------------
# HTTP boundary (Req 2.7)
# ---------------------------------------------------------------------------


class ServiceHandler(BaseHTTPRequestHandler):
    """Dispatches ``POST``/``GET`` requests onto the four engine operations.

    Routing is by method + path: ``POST /predict``, ``POST /savings``,
    ``GET /prices``, ``GET /regions``. Every response — success or error — is
    serialized as JSON with an ``application/json`` content type (Req 2.7).

    The per-operation bodies are implemented in later tasks; this skeleton owns
    the routing, request-body parsing, and JSON serialization.
    """

    #: Provider deadline (seconds) passed through to the per-request predictor
    #: and calibration. Matches the engine's 30-second default (Req 2.7).
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    # Silence the default stderr request logging so the service is quiet unless
    # explicitly asked; the Node_Server is the user-facing boundary.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # -- Dispatch -----------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802  (http.server naming)
        path = urlparse(self.path).path
        routes: Dict[str, Callable[[Dict[str, Any]], None]] = {
            "/predict": self._handle_predict,
            "/savings": self._handle_savings,
        }
        handler = routes.get(path)
        if handler is None:
            self._send_json(404, {"error": f"Route not found: {path}"})
            return
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._dispatch(lambda: handler(body))

    def do_GET(self) -> None:  # noqa: N802  (http.server naming)
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        routes: Dict[str, Callable[[Dict[str, list]], None]] = {
            "/prices": self._handle_prices,
            "/regions": self._handle_regions,
        }
        handler = routes.get(path)
        if handler is None:
            self._send_json(404, {"error": f"Route not found: {path}"})
            return
        self._dispatch(lambda: handler(query))

    def _dispatch(self, run: Callable[[], None]) -> None:
        """Run an operation handler, serializing unhandled errors as 500 JSON.

        Operation-specific engine-exception -> HTTP-status mapping (400/502) is
        added in a later task; this guard guarantees that even an unexpected
        failure yields a JSON body (Req 2.7) rather than a bare stack trace.
        """
        try:
            run()
        except NotImplementedError as exc:
            self._send_json(501, {"error": str(exc) or "Not implemented"})
        except Exception as exc:  # pragma: no cover - defensive catch-all
            self._send_json(500, {"error": f"Internal error: {exc}"})

    # -- Operation handlers (bodies filled in tasks 1.2-1.5) ---------------

    def _handle_predict(self, body: Dict[str, Any]) -> None:
        """``POST /predict`` — prediction with optional calibration (task 1.2).

        Implements the design "Prediction with optional calibration (Req 4)"
        flow: parse ``house`` (HouseProfile) and ``period`` (Period) from the
        request body, build a per-request predictor, and — when a valid
        ``reference_actual_usd`` (a number > 0) is supplied — run calibration to
        commit a tuned ``demand_scale`` onto the predictor before predicting.

        Calibration is best-effort (Req 4.5): an
        :class:`~energy_cost_predictor.errors.InvalidActualCostError` (rejected
        bill) or :class:`~energy_cost_predictor.errors.ProviderTimeoutError`
        leaves the predictor uncalibrated and sets ``calibration_applied`` to
        ``False`` while still returning a prediction. Other engine exceptions
        propagate to the dispatcher for the (task 1.5) status mapping.

        The response is ``{cost_usd, missing_intervals, calibration_applied,
        fallbacks}`` with ``missing_intervals`` serialized as ISO-8601 date
        strings and ``fallbacks`` taken from the shared per-request record
        (Req 8.5, 8.6).
        """
        house = _parse_house(body.get("house"))
        period = _parse_period(body.get("period"))

        predictor, fallbacks = build_predictor(timeout_seconds=self.timeout_seconds)

        calibration_applied = False
        reference_actual_usd = body.get("reference_actual_usd")
        if isinstance(reference_actual_usd, (int, float)) and not isinstance(
            reference_actual_usd, bool
        ) and reference_actual_usd > 0:
            # A valid bill amount was supplied: attempt calibration, committing
            # the tuned demand_scale onto the predictor (Req 4.2). A rejected
            # bill or a provider timeout falls back to an uncalibrated
            # prediction (Req 4.5).
            ref_provider = PlaceholderReferenceActualProvider(
                float(reference_actual_usd)
            )
            try:
                Calibration(predictor, house, period, ref_provider).calibrate()
                calibration_applied = True
            except (InvalidActualCostError, ProviderTimeoutError):
                calibration_applied = False

        result = predictor.predict_annual_cost(house, period)

        self._send_json(
            200,
            {
                "cost_usd": result.cost_usd,
                "missing_intervals": [
                    d.isoformat() for d in result.missing_intervals
                ],
                "calibration_applied": calibration_applied,
                "fallbacks": fallbacks.as_dict(),
            },
        )

    def _handle_savings(self, body: Dict[str, Any]) -> None:
        """``POST /savings`` — conventional vs heat-pump savings (task 1.3).

        Implements the design "Python_Service operation table" savings row:
        parse ``house`` (HouseProfile) and ``period`` (Period) from the request
        body, build a per-request predictor, and call
        :meth:`Predictor.compare_savings` — which requires an annual period of
        exactly 12 consecutive months (Req 2.6).

        The response is ``{conventional_cost, heat_pump_cost, savings,
        fallbacks}`` with each of the three cost values rounded to two decimal
        places (Req 2.2) and ``fallbacks`` taken from the shared per-request
        record (Req 8.5, 8.6). Engine exceptions propagate to the dispatcher for
        the (task 1.5) status mapping.
        """
        house = _parse_house(body.get("house"))
        period = _parse_period(body.get("period"))

        predictor, fallbacks = build_predictor(timeout_seconds=self.timeout_seconds)

        result = predictor.compare_savings(house, period)

        self._send_json(
            200,
            {
                "conventional_cost": round(result.conventional_cost, 2),
                "heat_pump_cost": round(result.heat_pump_cost, 2),
                "savings": round(result.savings, 2),
                "fallbacks": fallbacks.as_dict(),
            },
        )

    def _handle_prices(self, query: Dict[str, list]) -> None:
        """``GET /prices`` — monthly price-series synthesis (task 1.4)."""
        raise NotImplementedError("prices operation not yet implemented")

    def _handle_regions(self, query: Dict[str, list]) -> None:
        """``GET /regions`` — regions list synthesis (task 1.4)."""
        raise NotImplementedError("regions operation not yet implemented")

    # -- IO helpers ---------------------------------------------------------

    def _read_json_body(self) -> Dict[str, Any]:
        """Read and parse the request body as a JSON object.

        An absent body is treated as an empty object. A present but unparseable
        body, or a JSON value that is not an object, raises ``ValueError`` so the
        caller can respond 400.
        """
        length_header = self.headers.get("Content-Length")
        if not length_header:
            return {}
        try:
            length = int(length_header)
        except (TypeError, ValueError):
            raise ValueError("Invalid Content-Length header.")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request body is not valid JSON.")
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object.")
        return parsed

    def _send_json(self, status: int, payload: Any) -> None:
        """Serialize ``payload`` as JSON and write it with the given status.

        Sets an ``application/json`` content type on every response (Req 2.7).
        """
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Boot (Req: runnable as python -m energy_cost_predictor.service)
# ---------------------------------------------------------------------------


def make_server(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Create a :class:`ThreadingHTTPServer` bound to ``host:port``.

    Threading lets concurrent requests each build their own per-request
    :class:`Predictor` / :class:`Fallbacks` without shared mutable state.
    """
    return ThreadingHTTPServer((host, port), ServiceHandler)


def _resolve_port(argv: Optional[list] = None) -> int:
    """Resolve the listen port from CLI args, then ``PORT``, then the default.

    Accepts either a positional port (``python -m ... 9000``) or ``--port 9000``.
    An invalid value falls through to the environment / default.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    # --port N  or  --port=N
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            candidate = args[i + 1]
            if candidate.isdigit():
                return int(candidate)
        elif arg.startswith("--port="):
            candidate = arg.split("=", 1)[1]
            if candidate.isdigit():
                return int(candidate)

    # First bare positional integer.
    for arg in args:
        if arg.isdigit():
            return int(arg)

    env_port = os.environ.get("PORT")
    if env_port and env_port.isdigit():
        return int(env_port)

    return DEFAULT_PORT


def main(argv: Optional[list] = None) -> None:
    """Start the service and serve until interrupted.

    Runnable as ``python -m energy_cost_predictor.service`` on a configurable
    port (default 8000): ``PORT`` env var, a positional port, or ``--port``.
    """
    port = _resolve_port(argv)
    server = make_server(port)
    host, bound_port = server.server_address[:2]
    print(
        f"energy_cost_predictor.service listening on http://{host}:{bound_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
