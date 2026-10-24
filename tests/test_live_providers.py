"""Offline tests for the live weather and pricing providers.

These tests never touch the network: ``urllib.request.urlopen`` is monkeypatched
to return canned JSON payloads. They cover response parsing, unit conversion,
missing-day handling, endpoint selection, multi-fuel fetching with fallback, and
the error/guard paths.
"""

from __future__ import annotations

import io
import json
from datetime import date

import pytest

from energy_cost_predictor import live_weather_provider as lwp
from energy_cost_predictor import live_pricing_provider as lpp
from energy_cost_predictor.live_weather_provider import OpenMeteoWeatherProvider
from energy_cost_predictor.live_pricing_provider import (
    EIAPricingProvider,
    THERMS_PER_MCF,
    DEFAULT_OTHER_FUEL_PRICES,
)


class _FakeResponse(io.BytesIO):
    """Minimal context-manager stand-in for an http response object."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _json_response(payload):
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


def _price_row(value):
    return {"response": {"data": [{"period": "2024-01", "value": value}]}}


def _elec_row(cents):
    return {"response": {"data": [{"period": "2024-01", "price": cents}]}}


def _install_price_router(monkeypatch, *, elec, gas=None, oil=None, propane=None):
    """Route EIA requests to canned payloads by URL + series id.

    ``gas``/``oil``/``propane`` of None simulate an empty (unavailable) series so
    the provider's per-fuel fallback path is exercised.
    """

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if "electricity/retail-sales" in url:
            return _json_response(_elec_row(elec))
        if "natural-gas" in url:
            return _json_response(_price_row(gas) if gas is not None else {"response": {"data": []}})
        if "petroleum" in url:
            if "EPD2F" in url:  # heating oil series id fragment
                return _json_response(_price_row(oil) if oil is not None else {"response": {"data": []}})
            return _json_response(_price_row(propane) if propane is not None else {"response": {"data": []}})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(lpp.urllib.request, "urlopen", fake_urlopen)


# ---------------------------------------------------------------------------
# OpenMeteoWeatherProvider
# ---------------------------------------------------------------------------


def test_weather_parses_daily_temps_and_fills_missing(monkeypatch):
    payload = {
        "daily": {
            "time": ["2023-01-01", "2023-01-02", "2023-01-04"],
            "temperature_2m_mean": [30.5, None, 28.0],
        }
    }
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _json_response(payload)

    monkeypatch.setattr(lwp.urllib.request, "urlopen", fake_urlopen)

    provider = OpenMeteoWeatherProvider()
    series = provider.get_temperatures("MA", date(2023, 1, 1), date(2023, 1, 4))

    assert len(series.intervals) == 4
    by_date = {i.interval_date: i.outdoor_temp_f for i in series.intervals}
    assert by_date[date(2023, 1, 1)] == 30.5
    assert by_date[date(2023, 1, 2)] is None   # API null -> None
    assert by_date[date(2023, 1, 3)] is None   # absent day -> None
    assert by_date[date(2023, 1, 4)] == 28.0
    assert "temperature_unit=fahrenheit" in captured["url"]


def test_weather_uses_archive_endpoint_for_past_range(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _json_response({"daily": {"time": [], "temperature_2m_mean": []}})

    monkeypatch.setattr(lwp.urllib.request, "urlopen", fake_urlopen)
    OpenMeteoWeatherProvider().get_temperatures(
        "MA", date(2000, 1, 1), date(2000, 1, 2)
    )
    assert "archive-api.open-meteo.com" in seen["url"]


def test_weather_unknown_region_raises():
    with pytest.raises(KeyError):
        OpenMeteoWeatherProvider().get_temperatures(
            "ZZ", date(2023, 1, 1), date(2023, 1, 2)
        )


# ---------------------------------------------------------------------------
# EIAPricingProvider
# ---------------------------------------------------------------------------


def test_pricing_fetches_all_four_fuels_with_conversions(monkeypatch):
    # gas is $10.37/Mcf -> exactly $1.00/therm given THERMS_PER_MCF.
    _install_price_router(
        monkeypatch, elec=29.5, gas=THERMS_PER_MCF, oil=4.50, propane=3.25
    )
    table = EIAPricingProvider(api_key="dummy").get_prices("MA")

    assert table.prices["electricity_per_kwh"] == pytest.approx(0.295)      # cents -> $
    assert table.prices["natural_gas_per_therm"] == pytest.approx(1.00)     # $/Mcf -> $/therm
    assert table.prices["heating_oil_per_gallon"] == pytest.approx(4.50)    # $/gal direct
    assert table.prices["propane_per_gallon"] == pytest.approx(3.25)        # $/gal direct
    # Seasonal derived from the standard rate.
    assert table.prices["electricity_seasonal_per_kwh"] == pytest.approx(0.295 * 0.82, abs=1e-4)


def test_pricing_falls_back_per_fuel_when_series_unavailable(monkeypatch):
    # Oil + propane series empty -> each falls back to its constant; gas present.
    _install_price_router(
        monkeypatch, elec=20.0, gas=THERMS_PER_MCF * 2.0, oil=None, propane=None
    )
    table = EIAPricingProvider(api_key="dummy").get_prices("MA")

    assert table.prices["natural_gas_per_therm"] == pytest.approx(2.00)
    assert table.prices["heating_oil_per_gallon"] == DEFAULT_OTHER_FUEL_PRICES["heating_oil_per_gallon"]
    assert table.prices["propane_per_gallon"] == DEFAULT_OTHER_FUEL_PRICES["propane_per_gallon"]


def test_pricing_fetch_other_fuels_false_skips_extra_calls(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        assert "electricity/retail-sales" in req.full_url  # only electricity
        return _json_response(_elec_row(18.0))

    monkeypatch.setattr(lpp.urllib.request, "urlopen", fake_urlopen)
    table = EIAPricingProvider(api_key="dummy", fetch_other_fuels=False).get_prices("MA")

    assert calls["n"] == 1
    assert table.prices["electricity_per_kwh"] == pytest.approx(0.18)
    assert table.prices["natural_gas_per_therm"] == DEFAULT_OTHER_FUEL_PRICES["natural_gas_per_therm"]


def test_pricing_explicit_overrides_win(monkeypatch):
    _install_price_router(monkeypatch, elec=20.0, gas=THERMS_PER_MCF, oil=4.0, propane=3.0)
    provider = EIAPricingProvider(
        api_key="dummy",
        extra_prices={
            "heating_oil_per_gallon": 4.25,
            "electricity_seasonal_per_kwh": 0.15,
        },
    )
    table = provider.get_prices("MA")
    assert table.prices["heating_oil_per_gallon"] == 4.25          # override beats fetched 4.0
    assert table.prices["electricity_seasonal_per_kwh"] == 0.15    # override beats derived


def test_pricing_missing_key_raises(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        EIAPricingProvider(api_key=None).get_prices("MA")


def test_pricing_empty_electricity_raises(monkeypatch):
    monkeypatch.setattr(
        lpp.urllib.request, "urlopen",
        lambda req, timeout=None: _json_response({"response": {"data": []}}),
    )
    with pytest.raises(ValueError):
        EIAPricingProvider(api_key="dummy").get_prices("MA")


def test_pricing_unknown_region_raises():
    with pytest.raises(KeyError):
        EIAPricingProvider(api_key="dummy").get_prices("ZZ")
