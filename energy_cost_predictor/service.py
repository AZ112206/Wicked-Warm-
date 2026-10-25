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
    DependencyUnavailableError,
    EfficiencyOutOfRangeError,
    InvalidActualCostError,
    InvalidPeriodError,
    MissingFieldError,
    MissingRegionKeyError,
    ProviderDataError,
    ProviderTimeoutError,
    UnknownRegionError,
    UnsupportedFuelError,
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
    "UnsupportedMetricError",
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
# Price-series and regions synthesis (Req 6)
# ---------------------------------------------------------------------------


class UnsupportedMetricError(ValueError):
    """Raised when ``GET /prices`` is asked for a metric the service can't serve.

    Only ``electricity`` and ``gas`` are supported (Req 6.4). An unsupported
    metric is a client error; the status mapping (task 1.5) turns this into a
    400. It subclasses :class:`ValueError` so the current dispatcher degrades
    gracefully until that mapping lands.
    """

    def __init__(self, metric: str) -> None:
        self.metric = metric
        super().__init__(
            f"Unsupported metric '{metric}'. Supported metrics: electricity, gas."
        )


#: Lowest and highest ``months`` the service will synthesize. The Node layer
#: further restricts this to 1–12, but the service accepts the wider 1–60 range.
_MONTHS_MIN: int = 1
_MONTHS_MAX: int = 60
_MONTHS_DEFAULT: int = 12

#: Supported metrics -> (price-table key, display unit, dollars->unit scale).
#: Electricity is reported in cents per kWh (× 100 from the $/kWh table value);
#: gas is reported in the table's native $/therm (scale 1.0).
_METRIC_SPECS: Dict[str, Tuple[str, str, float]] = {
    "electricity": ("electricity_per_kwh", "¢/kWh", 100.0),
    "gas": ("natural_gas_per_therm", "$/therm", 1.0),
}

#: Three-letter calendar month labels indexed by month number (1 == Jan).
_MONTH_LABELS: Tuple[str, ...] = (
    "",  # 1-based: index 0 unused
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

#: Deterministic seasonal shape multipliers keyed by calendar month (1 == Jan).
#: Heating-season months are relatively dearer (demand/price pressure peaks in
#: deep winter) and shoulder/summer months softer — the same "fixed seasonal
#: curve" idea as the engine's synthetic weather shape. These are *relative*
#: multipliers; the series is rescaled so its final point equals the anchored
#: current price, so only the ratios between months matter.
_SEASONAL_MULTIPLIERS: Dict[int, float] = {
    1: 1.08,   # Jan — deep winter peak
    2: 1.06,   # Feb
    3: 1.02,   # Mar
    4: 0.98,   # Apr
    5: 0.94,   # May
    6: 0.92,   # Jun — summer trough
    7: 0.92,   # Jul
    8: 0.93,   # Aug
    9: 0.95,   # Sep
    10: 0.99,  # Oct
    11: 1.03,  # Nov — heating season ramp
    12: 1.07,  # Dec
}

#: Placeholder display regions and their fixed ¢/kWh offset off the MA rate.
#: Clearly synthetic (Req 6.3); flagged via ``fallbacks.regions_placeholder``.
_PLACEHOLDER_REGION_OFFSETS: Tuple[Tuple[str, float], ...] = (
    ("Northeast", 1.5),
    ("Midwest", -2.0),
    ("South", -3.5),
    ("West", 0.75),
)


def _single_query_value(
    query: Dict[str, list], key: str, default: Optional[str]
) -> Optional[str]:
    """Return the first value for ``key`` in a ``parse_qs`` dict, or ``default``.

    ``parse_qs`` maps each key to a list of values; a repeated parameter keeps
    the first occurrence.
    """
    values = query.get(key)
    if not values:
        return default
    return values[0]


def _metric_spec(metric: Optional[str]) -> Tuple[str, str, float]:
    """Resolve a metric name to its ``(price_key, unit, scale)`` spec.

    :raises UnsupportedMetricError: If ``metric`` is not one of the supported
        values (Req 6.4).
    """
    spec = _METRIC_SPECS.get(metric or "")
    if spec is None:
        raise UnsupportedMetricError("" if metric is None else metric)
    return spec


def _parse_months(raw: Optional[str]) -> int:
    """Parse and range-check the ``months`` query value (service range 1–60).

    An absent value defaults to :data:`_MONTHS_DEFAULT`. A present value must be
    an integer in the inclusive range ``[_MONTHS_MIN, _MONTHS_MAX]``.

    :raises ValueError: If ``months`` is non-integer or out of range (the status
        mapping turns this into a 400).
    """
    if raw is None or raw.strip() == "":
        return _MONTHS_DEFAULT
    try:
        months = int(raw.strip())
    except ValueError:
        raise ValueError(f"'months' must be an integer, got '{raw}'.")
    if months < _MONTHS_MIN or months > _MONTHS_MAX:
        raise ValueError(
            f"'months' must be between {_MONTHS_MIN} and {_MONTHS_MAX}, got {months}."
        )
    return months


def _current_metric_price(
    predictor: Predictor, price_key: str, scale: float
) -> float:
    """Read the current point price for ``price_key`` from the active provider.

    Fetches the Massachusetts price table through the predictor's guarding
    pricing adapter (so any live-source failure already flipped
    ``fallbacks.pricing`` before this returns), multiplies by ``scale`` to
    convert units (×100 for cents), and rounds to two decimal places. A missing
    key or a negative table value is clamped to a non-negative result so the
    synthesized series and region rates stay ≥ 0 (Req 6.2, 6.3).
    """
    table: PriceTable = predictor.pricing_adapter.get_prices("MA")
    raw = table.prices.get(price_key, 0.0)
    value = max(float(raw) * scale, 0.0)
    return round(value, 2)


def _synthesize_price_points(current_price: float, months: int) -> list:
    """Build a trailing ``months``-long seasonal series ending at the latest month.

    Only the final point is a real observation (the anchored current price); the
    earlier points are a derived trailing estimate produced by a deterministic
    seasonal shape (:data:`_SEASONAL_MULTIPLIERS`) rescaled so the final point
    equals ``current_price``. The series is ordered oldest -> newest and ends
    with the current calendar month, so it always carries exactly ``months``
    points, each with a non-empty month label and a value ≥ 0 (Req 6.1, 6.2).
    """
    current_month = date.today().month

    # Walk back ``months - 1`` calendar months from the current month so the
    # series is ordered oldest -> newest and ends at the most recent month.
    month_numbers = []
    for offset in range(months - 1, -1, -1):
        # ((current - offset - 1) mod 12) + 1 maps to a 1..12 month number.
        month_numbers.append(((current_month - offset - 1) % 12) + 1)

    anchor_multiplier = _SEASONAL_MULTIPLIERS[current_month]

    points = []
    for month_number in month_numbers:
        multiplier = _SEASONAL_MULTIPLIERS[month_number]
        # Rescale so the final (current) month lands exactly on current_price.
        value = current_price * (multiplier / anchor_multiplier)
        points.append(
            {"month": _MONTH_LABELS[month_number], "value": round(max(value, 0.0), 2)}
        )

    # Guarantee the final point equals the anchored current price exactly,
    # independent of floating-point rounding above.
    if points:
        points[-1]["value"] = round(max(current_price, 0.0), 2)

    return points


def _percent_change(points: list) -> float:
    """Return the percent delta between the last two synthesized points.

    Used for a region's ``change`` field. With fewer than two points, or a
    zero prior value, the change is ``0.0``. Rounded to two decimal places.
    """
    if len(points) < 2:
        return 0.0
    prev = points[-2]["value"]
    last = points[-1]["value"]
    if prev == 0:
        return 0.0
    return round((last - prev) / prev * 100.0, 2)


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

    #: Engine exceptions that are client errors -> HTTP 400 (Req 2.8, 2.9, 2.10,
    #: 9.2, 9.3). Each already carries a descriptive message naming the offending
    #: field/reason/key, so ``str(exc)`` is used verbatim as the error body.
    #: ``UnsupportedMetricError`` and the out-of-1–60 ``months`` case are plain
    #: ``ValueError`` instances handled by the ``ValueError`` branch below.
    _CLIENT_ERRORS: Tuple[type, ...] = (
        MissingFieldError,
        InvalidPeriodError,
        MissingRegionKeyError,
        UnknownRegionError,
        UnsupportedFuelError,
        EfficiencyOutOfRangeError,
    )

    #: Engine exceptions that mean an upstream data source is unavailable even
    #: after per-source fallback -> HTTP 502 (Req 1.8, 8.7). ``ProviderTimeoutError``
    #: and ``ProviderDataError`` only reach the dispatcher if they escaped the
    #: guarding providers (a non-fallback path); ``DependencyUnavailableError``
    #: signals a missing required dependency.
    _UPSTREAM_ERRORS: Tuple[type, ...] = (
        DependencyUnavailableError,
        ProviderTimeoutError,
        ProviderDataError,
    )

    def _dispatch(self, run: Callable[[], None]) -> None:
        """Run an operation handler, mapping engine exceptions to HTTP status.

        Maps the engine's typed exceptions to the design's "Engine exception ->
        HTTP status" table:

        - Client errors (missing field, invalid period, missing/unknown region,
          unsupported fuel, efficiency out of range) -> **400** naming the cause
          via ``str(exc)`` (Req 2.8, 2.9, 9.2, 9.3).
        - An unsupported ``metric`` (:class:`UnsupportedMetricError`) and an
          out-of-1–60 ``months`` value (a plain :class:`ValueError` from
          :func:`_parse_months`) -> **400** (Req 2.3, 2.10, 6.4). These are the
          only ``ValueError`` instances that reach the dispatcher from the GET
          handlers; POST body-parsing ``ValueError`` instances are handled
          upstream in :meth:`do_POST`.
        - Upstream-unavailable errors surviving per-source fallback
          (:class:`DependencyUnavailableError`, :class:`ProviderTimeoutError`,
          :class:`ProviderDataError`) -> **502** (Req 1.8, 8.7).

        Every response is JSON ``{error: <message>}`` (Req 2.7). A final
        catch-all turns any truly unexpected error into a defensive 500.
        """
        try:
            run()
        except self._CLIENT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except self._UPSTREAM_ERRORS as exc:
            self._send_json(502, {"error": str(exc)})
        except ValueError as exc:
            # UnsupportedMetricError (a ValueError subclass) and the out-of-range
            # ``months`` ValueError from the GET handlers (Req 2.10, 6.4, 2.3).
            self._send_json(400, {"error": str(exc)})
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
        """``GET /prices`` — monthly price-series synthesis (task 1.4).

        Implements the design "Price series and regions synthesis (Req 6)"
        price-series flow. Supports two metrics (Req 6.4):

        =============  ==========  ================================
        ``metric``     unit        price-table key -> point price
        =============  ==========  ================================
        ``electricity``  ``¢/kWh``   ``electricity_per_kwh`` × 100
        ``gas``          ``$/therm`` ``natural_gas_per_therm``
        =============  ==========  ================================

        Any other ``metric`` raises :class:`UnsupportedMetricError` (a
        ``ValueError`` subclass) naming the value, which the status mapping
        (task 1.5) turns into a 400 (Req 2.10, 6.4). ``months`` must be an
        integer in the service range 1–60; anything else raises ``ValueError``.
        ``months`` defaults to 12 when absent.

        The current point price is read from the active pricing provider via
        :func:`build_predictor` (live ``EIAPricingProvider`` or placeholder on
        fallback), and a trailing ``months``-long deterministic seasonal series
        is anchored so the final point equals that current price. The response
        is ``{metric, unit, points, fallbacks}`` with exactly ``months`` ordered
        points — each a non-empty month label (``"Nov"``, ``"Dec"``, …) and a
        value ≥ 0 — ending with the most recent calendar month (Req 6.1, 6.2).
        Only the latest point is a real observation; the earlier points are a
        derived trailing estimate (see :func:`_synthesize_price_points`).
        """
        metric = _single_query_value(query, "metric", default="electricity")
        months = _parse_months(_single_query_value(query, "months", default=None))

        price_key, unit, scale = _metric_spec(metric)

        predictor, fallbacks = build_predictor(timeout_seconds=self.timeout_seconds)
        current_price = _current_metric_price(predictor, price_key, scale)

        points = _synthesize_price_points(current_price, months)

        self._send_json(
            200,
            {
                "metric": metric,
                "unit": unit,
                "points": points,
                "fallbacks": fallbacks.as_dict(),
            },
        )

    def _handle_regions(self, query: Dict[str, list]) -> None:
        """``GET /regions`` — regions list synthesis (task 1.4).

        Implements the design "Price series and regions synthesis (Req 6)"
        regions flow. The engine configures only Massachusetts as a real region,
        so the service returns one real MA entry built from the current
        electricity price (``rate`` in ¢/kWh from ``electricity_per_kwh`` × 100,
        ``change`` = the percent delta between the last two synthesized monthly
        electricity points) plus clearly-labeled placeholder entries for the
        other display regions the frontend table expects — Northeast, Midwest,
        South, and West — each a non-negative rate generated from a fixed offset
        off the MA rate (Req 6.3).

        The placeholder regions are flagged via ``fallbacks.regions_placeholder
        = true``. The response is ``{regions: [{region, rate, change}],
        fallbacks}``; the ``regions_placeholder`` flag is added to the fallbacks
        dict for this response only so the predict/savings responses keep their
        ``{weather, pricing}`` shape.
        """
        predictor, fallbacks = build_predictor(timeout_seconds=self.timeout_seconds)

        # MA real entry: electricity rate in ¢/kWh and the month-over-month
        # change taken from the last two synthesized electricity points.
        ma_rate = _current_metric_price(predictor, "electricity_per_kwh", scale=100.0)
        electricity_points = _synthesize_price_points(ma_rate, months=12)
        change = _percent_change(electricity_points)

        regions = [{"region": "Massachusetts", "rate": ma_rate, "change": change}]

        # Placeholder display regions from fixed offsets off the MA rate; each
        # rate is clamped to be non-negative (Req 6.3).
        for region_name, offset in _PLACEHOLDER_REGION_OFFSETS:
            rate = round(max(ma_rate + offset, 0.0), 2)
            regions.append({"region": region_name, "rate": rate, "change": 0.0})

        self._send_json(
            200,
            {
                "regions": regions,
                "fallbacks": {**fallbacks.as_dict(), "regions_placeholder": True},
            },
        )

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


def _load_dotenv() -> None:
    """Load KEY=value pairs from a project-root ``.env`` into ``os.environ``.

    A tiny, dependency-free loader (the engine is stdlib-only). It reads the
    ``.env`` file at the repository root — two levels up from this module — so
    the service picks up ``EIA_API_KEY`` the same way the Node_Server picks it up
    via dotenv. Existing environment variables are NOT overwritten, so a value
    exported in the shell still wins. Lines that are blank, comments (``#``), or
    lack an ``=`` are ignored; surrounding quotes on the value are stripped. Any
    read error is swallowed — a missing or unreadable ``.env`` simply means the
    relevant providers fall back to placeholders.
    """
    import os.path

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"").strip("\x27")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        # No .env (or unreadable): providers fall back to placeholders.
        return


def main(argv: Optional[list] = None) -> None:
    """Start the service and serve until interrupted.

    Runnable as ``python -m energy_cost_predictor.service`` on a configurable
    port (default 8000): ``PORT`` env var, a positional port, or ``--port``.

    Loads a project-root ``.env`` (if present) before starting so ``EIA_API_KEY``
    is available to the pricing provider without a manual shell export.
    """
    _load_dotenv()
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
