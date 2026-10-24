"""Provider-adapter boundary: field and timeout guards (Req 2.6, 2.7, 8.7).

This module is the only I/O-aware layer in the package. It wraps each provider
call so that:

* A call that does not return within a deadline (default 30 seconds) raises
  :class:`~energy_cost_predictor.errors.ProviderTimeoutError` naming the
  provider (Req 2.7, 8.7). The wait uses a ``concurrent.futures`` style
  deadline (:class:`concurrent.futures.ThreadPoolExecutor` +
  ``future.result(timeout=...)``).
* A returned object whose required fields are absent, ``None``, or empty raises
  :class:`~energy_cost_predictor.errors.ProviderDataError` naming the provider
  and each missing field (Req 2.6).

The pure core never imports this module and never sees a timeout: adapters are
the single timeout-aware seam. The adapter wrappers implement the same
``typing.Protocol`` shapes as the underlying providers
(:mod:`energy_cost_predictor.providers`), so a wrapped provider drops into the
core wherever a raw provider would (Req 2.1-2.5).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date
from typing import Callable, List, TypeVar

from .errors import ProviderDataError, ProviderTimeoutError
from .models import PriceTable, ReferenceActual, WeatherSeries
from .providers import PricingProvider, ReferenceActualProvider, WeatherProvider

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "call_with_deadline",
    "validate_weather_series",
    "validate_price_table",
    "validate_reference_actual",
    "WeatherProviderAdapter",
    "PricingProviderAdapter",
    "ReferenceActualProviderAdapter",
]

#: Default provider deadline in seconds (Req 2.7, 8.7). Configurable per adapter
#: so tests can use a short timeout.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

T = TypeVar("T")


def call_with_deadline(
    provider_name: str,
    func: Callable[[], T],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> T:
    """Run ``func`` under a deadline, raising ``ProviderTimeoutError`` on expiry.

    The call runs on a worker thread and is awaited with
    ``future.result(timeout=...)``. If the deadline passes before the provider
    responds, a :class:`ProviderTimeoutError` naming ``provider_name`` is raised
    (Req 2.7, 8.7).

    Note: Python cannot forcibly kill the worker thread, so a hung provider call
    keeps running in the background after the timeout fires; the adapter does not
    wait for it (``shutdown(wait=False)``) and the caller's estimate is halted
    immediately as required.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(func)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            raise ProviderTimeoutError(provider_name, timeout_seconds) from None
    finally:
        # Do not block on a still-running (hung) provider call.
        executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Required-field validation (Req 2.6)
# ---------------------------------------------------------------------------


def validate_weather_series(
    provider_name: str, series: WeatherSeries
) -> WeatherSeries:
    """Validate a :class:`WeatherSeries` has its required fields (Req 2.6).

    Required fields: a non-empty ``region_key`` and a non-empty ``intervals``
    sequence. Individual ``None`` outdoor temperatures *inside* intervals are
    valid missing data handled by the demand model (Req 4.6) and are NOT an
    error here — only an absent/empty whole series or a missing region key is a
    :class:`ProviderDataError`.
    """
    missing: List[str] = []
    if series is None:
        raise ProviderDataError(provider_name, ["series"])
    if _is_empty_str(getattr(series, "region_key", None)):
        missing.append("region_key")
    intervals = getattr(series, "intervals", None)
    if intervals is None or len(intervals) == 0:
        missing.append("intervals")
    if missing:
        raise ProviderDataError(provider_name, missing)
    return series


def validate_price_table(provider_name: str, table: PriceTable) -> PriceTable:
    """Validate a :class:`PriceTable` has its required fields (Req 2.6).

    Required fields: a non-empty ``region_key`` and a non-empty ``prices``
    mapping. Negative or individually-invalid price values are the Cost_Model's
    concern (Req 5.6, 6.8); here only an absent/empty table structure is a
    :class:`ProviderDataError`.
    """
    missing: List[str] = []
    if table is None:
        raise ProviderDataError(provider_name, ["prices"])
    if _is_empty_str(getattr(table, "region_key", None)):
        missing.append("region_key")
    prices = getattr(table, "prices", None)
    if prices is None or len(prices) == 0:
        missing.append("prices")
    if missing:
        raise ProviderDataError(provider_name, missing)
    return table


def validate_reference_actual(
    provider_name: str, actual: ReferenceActual
) -> ReferenceActual:
    """Validate a :class:`ReferenceActual` has its required fields (Req 2.6).

    Required fields: a non-empty ``house_id`` and a present (non-null)
    ``actual_annual_cost_usd``. The <= 0 rejection is calibration's concern
    (Req 8.2); here only an absent/null value is a :class:`ProviderDataError`.
    """
    missing: List[str] = []
    if actual is None:
        raise ProviderDataError(provider_name, ["house_id", "actual_annual_cost_usd"])
    if _is_empty_str(getattr(actual, "house_id", None)):
        missing.append("house_id")
    if getattr(actual, "actual_annual_cost_usd", None) is None:
        missing.append("actual_annual_cost_usd")
    if missing:
        raise ProviderDataError(provider_name, missing)
    return actual


def _is_empty_str(value: object) -> bool:
    """True when a required string-like field is absent, null, or blank."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


# ---------------------------------------------------------------------------
# Typed adapter wrappers (implement the same Protocol shapes)
# ---------------------------------------------------------------------------


class WeatherProviderAdapter:
    """Timeout- and field-guarded wrapper around a :class:`WeatherProvider`.

    Implements the :class:`WeatherProvider` Protocol shape, so the core can use
    it wherever a raw weather provider would appear (Req 2.1-2.5).
    """

    def __init__(
        self,
        provider: WeatherProvider,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        provider_name: str = "WeatherProvider",
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._provider_name = provider_name

    def get_temperatures(
        self, region_key: str, start: date, end: date
    ) -> WeatherSeries:
        series = call_with_deadline(
            self._provider_name,
            lambda: self._provider.get_temperatures(region_key, start, end),
            self._timeout_seconds,
        )
        return validate_weather_series(self._provider_name, series)


class PricingProviderAdapter:
    """Timeout- and field-guarded wrapper around a :class:`PricingProvider`.

    Implements the :class:`PricingProvider` Protocol shape (Req 2.1-2.5).
    """

    def __init__(
        self,
        provider: PricingProvider,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        provider_name: str = "PricingProvider",
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._provider_name = provider_name

    def get_prices(self, region_key: str) -> PriceTable:
        table = call_with_deadline(
            self._provider_name,
            lambda: self._provider.get_prices(region_key),
            self._timeout_seconds,
        )
        return validate_price_table(self._provider_name, table)


class ReferenceActualProviderAdapter:
    """Timeout- and field-guarded wrapper around a ``ReferenceActualProvider``.

    Implements the :class:`ReferenceActualProvider` Protocol shape (Req 2.1-2.5,
    8.6, 8.7).
    """

    def __init__(
        self,
        provider: ReferenceActualProvider,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        provider_name: str = "ReferenceActualProvider",
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._provider_name = provider_name

    def get_reference_actual(self, house_id: str) -> ReferenceActual:
        actual = call_with_deadline(
            self._provider_name,
            lambda: self._provider.get_reference_actual(house_id),
            self._timeout_seconds,
        )
        return validate_reference_actual(self._provider_name, actual)
