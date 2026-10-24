"""Seasonal electricity rate selection for the Energy Cost Predictor.

This module implements Core Math section 4 (Seasonal rate application) from the
design document. Massachusetts defines a :class:`SeasonalRateRule` whose
discounted electric delivery rate applies to heat-pump electricity consumed from
November 1 through April 30 inclusive; outside that window the standard rate
applies (Req 6.6, 6.7).

Two pure functions are provided:

* :func:`is_in_season` — a calendar predicate that answers whether a given date
  falls inside a region's seasonal window (and whether the region even has one).
* :func:`rate_for_date` — the rate selector that returns the seasonal unit price
  when in season and a seasonal rule is defined, otherwise the standard price.

The module depends only on the data models and the typed error hierarchy; it
imports no providers and performs no I/O. It uses only the standard library
(``datetime``).
"""

from __future__ import annotations

from datetime import date

from .errors import PriceUnavailableError
from .models import PriceTable, RegionProfile

__all__ = [
    "is_in_season",
    "rate_for_date",
]


def is_in_season(region: RegionProfile, d: date) -> bool:
    """Return whether date ``d`` falls within ``region``'s seasonal rate window.

    The window is defined by the region's :class:`SeasonalRateRule` as inclusive
    ``(month, day)`` endpoints. For Massachusetts this is November 1 through
    April 30, which wraps the calendar-year boundary: dates from the season start
    through the end of December and from the beginning of January through the
    season end are both in season. Dates exactly on the start (Nov 1) and end
    (Apr 30) are in season (Req 6.6).

    Args:
        region: The region whose seasonal window is consulted. A region without
            a :class:`SeasonalRateRule` is never in season.
        d: The interval date to test.

    Returns:
        ``True`` iff ``region`` defines a seasonal rate and ``d`` falls within
        its inclusive window; ``False`` otherwise.
    """
    rule = region.seasonal_rate
    if rule is None:
        return False

    start = rule.season_start   # e.g. (11, 1) -> Nov 1
    end = rule.season_end       # e.g. (4, 30) -> Apr 30

    # Compare by (month, day) so the test is year-agnostic. Tuple comparison
    # gives the right inclusive ordering within a month and across months.
    current = (d.month, d.day)

    if start <= end:
        # Non-wrapping window (same calendar year): start .. end inclusive.
        return start <= current <= end

    # Wrapping window (e.g. Nov 1 .. Apr 30): in season if on/after the start
    # (through Dec 31) OR on/before the end (from Jan 1).
    return current >= start or current <= end


def rate_for_date(region: RegionProfile, prices: PriceTable, d: date) -> float:
    """Return the electricity unit price that applies to date ``d``.

    When ``region`` defines a seasonal rate and ``d`` is in season, the seasonal
    (discounted) delivery price is used; otherwise the standard electricity price
    is used (Req 6.6, 6.7). Implements the ``rate_d`` selection from Core Math
    section 4.

    Args:
        region: The region supplying the (optional) seasonal rate rule and the
            price keys to look up.
        prices: The :class:`PriceTable` holding unit prices for the region.
        d: The interval date whose applicable rate is selected.

    Returns:
        The applicable electricity unit price in dollars per kWh.

    Raises:
        PriceUnavailableError: If the required price key is absent from the price
            table or its value is negative. The error names the offending key and
            no fallback or default price is substituted (Req 6.8).
    """
    rule = region.seasonal_rate
    use_seasonal = rule is not None and is_in_season(region, d)

    if use_seasonal:
        # rule is not None is guaranteed by use_seasonal.
        assert rule is not None
        price_key = rule.seasonal_price_key
    elif rule is not None:
        price_key = rule.price_key
    else:
        # No seasonal rule defined: fall back to the region's electricity fuel
        # standard price key. This keeps the selector usable for regions without
        # a seasonal rule while still naming a concrete key on error.
        price_key = _standard_price_key(region)

    return _lookup_price(prices, price_key)


def _standard_price_key(region: RegionProfile) -> str:
    """Return the standard electricity price key for a region without a rule.

    Prefers the ``electric_resistance`` fuel's price key when present, since that
    is the electricity price key used across the region configuration.
    """
    electric = region.fuels.get("electric_resistance")
    if electric is not None:
        return electric.price_key
    # Fall back to a conventional electricity key name so the missing-price
    # error still names a concrete key.
    return "electricity_per_kwh"


def _lookup_price(prices: PriceTable, price_key: str) -> float:
    """Return a non-negative price for ``price_key`` or raise.

    Raises:
        PriceUnavailableError: If the key is missing, ``None``, or negative
            (Req 6.8). The error names the key (and the bad value when present).
    """
    value = prices.prices.get(price_key)
    if value is None:
        raise PriceUnavailableError(price_key)
    if value < 0:
        raise PriceUnavailableError(price_key, value)
    return float(value)
