"""Live pricing provider backed by the EIA API (v2).

The U.S. Energy Information Administration (https://www.eia.gov/opendata)
publishes energy prices through a free JSON API. This provider implements the
``PricingProvider`` Protocol shape (:mod:`energy_cost_predictor.providers`) by
fetching residential prices for all four heating fuels the model supports and
returning them as a :class:`~energy_cost_predictor.models.PriceTable`.

Fuels and EIA routes
--------------------
- **Electricity** - ``electricity/retail-sales`` route, residential (RES) price
  in cents/kWh -> converted to $/kWh (``electricity_per_kwh``).
- **Natural gas** - ``natural-gas/pri/sum`` route, residential price in
  $/thousand-cubic-feet (Mcf) -> converted to $/therm via
  :data:`THERMS_PER_MCF` (``natural_gas_per_therm``).
- **Heating oil** - ``petroleum/pri/wfr`` route, New England (PADD 1A)
  residential No. 2 heating oil in $/gallon (``heating_oil_per_gallon``).
- **Propane** - ``petroleum/pri/wfr`` route, New England residential propane in
  $/gallon (``propane_per_gallon``).

Robustness and gaps
-------------------
- Heating-oil and propane series are collected weekly only Oct-Mar (monthly
  Apr-Sep); the most recent available value is used year-round.
- If a non-electric fuel fetch fails or returns nothing, the provider falls back
  to the configured constant in :data:`DEFAULT_OTHER_FUEL_PRICES` (overridable
  via ``extra_prices``) rather than failing the whole request - electricity is
  the one hard requirement. Set ``fetch_other_fuels=False`` to skip the extra
  calls entirely and use the constants.
- **Seasonal heat-pump rate:** EIA does not publish the utility-specific
  Nov 1-Apr 30 heat-pump delivery tariff. It defaults to a configurable discount
  off the fetched electricity price. In practice, calibrating against a real
  utility bill already absorbs a customer's actual seasonal rate, so this is an
  optional override (pass ``electricity_seasonal_per_kwh`` in ``extra_prices``)
  rather than a required input.

Setup
-----
Get a free API key at https://www.eia.gov/opendata/ and set the ``EIA_API_KEY``
environment variable (or pass ``api_key=``).

This module performs network I/O and lives outside the pure core. Wrap it in
:class:`~energy_cost_predictor.provider_adapter.PricingProviderAdapter` (as the
``Predictor`` does) for timeout and field guards. The required electricity price
raises on failure rather than being fabricated.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Dict, List, Mapping, Optional, Tuple

from .models import PriceTable

__all__ = [
    "EIAPricingProvider",
    "DEFAULT_OTHER_FUEL_PRICES",
    "THERMS_PER_MCF",
]


#: EIA APIv2 route bases.
_EIA_ELECTRICITY_URL = "https://api.eia.gov/v2/electricity/retail-sales/data/"
_EIA_NATGAS_URL = "https://api.eia.gov/v2/natural-gas/pri/sum/data/"
_EIA_PETROLEUM_URL = "https://api.eia.gov/v2/petroleum/pri/wfr/data/"

#: 1 thousand cubic feet (Mcf) of natural gas ~= 1.037 MMBtu = 10.37 therms.
#: EIA reports residential gas in $/Mcf; divide by this to get $/therm.
THERMS_PER_MCF: float = 10.37

#: Maps a region key to the EIA state id used for the ``stateid`` facet.
_REGION_STATE_ID: Dict[str, str] = {
    "MA": "MA",
}

#: EIA series ids for the New England (PADD 1A) residential petroleum products.
#: These serve heating oil (No. 2) and propane in $/gallon directly.
_PETROLEUM_SERIES: Dict[str, Dict[str, str]] = {
    "MA": {
        "heating_oil_per_gallon": "EMD_EPD2F_PRS_R1A_DPG",   # No. 2 heating oil, residential, PADD 1A
        "propane_per_gallon": "EER_EPLLPA_PRS_R1A_DPG",      # propane, residential, PADD 1A
    },
}

#: Fallback non-electric fuel prices used when a live fetch is unavailable or
#: disabled, and overridable via ``extra_prices``. (USD per listed unit.)
DEFAULT_OTHER_FUEL_PRICES: Dict[str, float] = {
    "natural_gas_per_therm": 2.50,
    "heating_oil_per_gallon": 6.00,
    "propane_per_gallon": 3.00,
}

#: Fraction of the standard electricity price used for the seasonal heat-pump
#: delivery rate when no explicit seasonal price is provided.
_DEFAULT_SEASONAL_DISCOUNT = 0.82


class EIAPricingProvider:
    """Residential heating-fuel prices for a region from the EIA API (Req 2.2).

    :param api_key: EIA API key. Falls back to the ``EIA_API_KEY`` env var.
    :param extra_prices: Prices merged into / overriding fetched values (e.g. an
        explicit ``electricity_seasonal_per_kwh`` or a fuel constant). These take
        precedence over both the live fetch fallbacks and
        :data:`DEFAULT_OTHER_FUEL_PRICES`.
    :param fetch_other_fuels: When True (default), fetch natural gas, heating
        oil, and propane live; when False, use the constants for those fuels.
    :param seasonal_discount: Multiplier applied to the standard electricity
        price to derive the seasonal rate when one is not supplied explicitly.
    :param timeout_seconds: Lower-level socket timeout per request.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        extra_prices: Optional[Mapping[str, float]] = None,
        fetch_other_fuels: bool = True,
        seasonal_discount: float = _DEFAULT_SEASONAL_DISCOUNT,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("EIA_API_KEY")
        self._overrides = dict(extra_prices) if extra_prices else {}
        self._fetch_other_fuels = fetch_other_fuels
        self._seasonal_discount = seasonal_discount
        self._timeout_seconds = timeout_seconds

    # -- HTTP helpers -------------------------------------------------------

    def _get_json(self, url: str, query: List[Tuple[str, str]]) -> dict:
        full = f"{url}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(
            full, headers={"User-Agent": "energy-cost-predictor"}
        )
        with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _latest_value(self, payload: dict) -> Optional[float]:
        """Return the most recent numeric ``value``/``price`` in a response."""
        rows = (payload.get("response") or {}).get("data") or []
        if not rows:
            return None
        row = rows[0]
        raw = row.get("value", row.get("price"))
        return None if raw is None else float(raw)

    # -- Per-fuel fetchers --------------------------------------------------

    def _fetch_electricity(self, state_id: str) -> float:
        """Most recent monthly residential electricity price, in $/kWh."""
        query = [
            ("api_key", self._api_key),
            ("frequency", "monthly"),
            ("data[0]", "price"),
            ("facets[sectorid][]", "RES"),
            ("facets[stateid][]", state_id),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("offset", "0"),
            ("length", "1"),
        ]
        cents = self._latest_value(self._get_json(_EIA_ELECTRICITY_URL, query))
        if cents is None:
            raise ValueError("EIA returned no residential electricity price.")
        return round(cents / 100.0, 4)

    def _fetch_natural_gas(self, state_id: str) -> Optional[float]:
        """Most recent residential natural-gas price in $/therm, or None."""
        query = [
            ("api_key", self._api_key),
            ("frequency", "monthly"),
            ("data[0]", "value"),
            ("facets[process][]", "PRS"),      # residential price series
            ("facets[duoarea][]", f"S{state_id}"),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("offset", "0"),
            ("length", "1"),
        ]
        dollars_per_mcf = self._latest_value(self._get_json(_EIA_NATGAS_URL, query))
        if dollars_per_mcf is None:
            return None
        return round(dollars_per_mcf / THERMS_PER_MCF, 4)

    def _fetch_petroleum(self, region_key: str, series_id: str) -> Optional[float]:
        """Most recent residential petroleum price in $/gallon, or None."""
        query = [
            ("api_key", self._api_key),
            ("frequency", "weekly"),
            ("data[0]", "value"),
            ("facets[series][]", series_id),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "desc"),
            ("offset", "0"),
            ("length", "1"),
        ]
        value = self._latest_value(self._get_json(_EIA_PETROLEUM_URL, query))
        return None if value is None else round(value, 4)

    # -- Public API ---------------------------------------------------------

    def get_prices(self, region_key: str) -> PriceTable:
        """Return a PriceTable for ``region_key`` with live residential prices.

        Electricity is always fetched (and is the one hard requirement). Natural
        gas, heating oil, and propane are fetched when ``fetch_other_fuels`` is
        set, each falling back to its configured constant if the live value is
        unavailable. Explicit ``extra_prices`` override everything. The seasonal
        electricity key is derived from the standard rate unless supplied.
        """
        if not self._api_key:
            raise RuntimeError(
                "EIA API key missing: set the EIA_API_KEY environment variable "
                "or pass api_key= to EIAPricingProvider."
            )
        state_id = _REGION_STATE_ID.get(region_key)
        if state_id is None:
            raise KeyError(
                f"No EIA state id configured for region {region_key!r}; "
                f"add it to _REGION_STATE_ID."
            )

        prices: Dict[str, float] = {}

        # Electricity: required.
        electricity_per_kwh = self._fetch_electricity(state_id)
        prices["electricity_per_kwh"] = electricity_per_kwh

        # Non-electric fuels: live when enabled, else constants; each fuel falls
        # back to its constant independently so one missing series never sinks
        # the whole table.
        gas = oil = propane = None
        if self._fetch_other_fuels:
            try:
                gas = self._fetch_natural_gas(state_id)
            except Exception:
                gas = None
            series = _PETROLEUM_SERIES.get(region_key, {})
            if "heating_oil_per_gallon" in series:
                try:
                    oil = self._fetch_petroleum(region_key, series["heating_oil_per_gallon"])
                except Exception:
                    oil = None
            if "propane_per_gallon" in series:
                try:
                    propane = self._fetch_petroleum(region_key, series["propane_per_gallon"])
                except Exception:
                    propane = None

        prices["natural_gas_per_therm"] = (
            gas if gas is not None else DEFAULT_OTHER_FUEL_PRICES["natural_gas_per_therm"]
        )
        prices["heating_oil_per_gallon"] = (
            oil if oil is not None else DEFAULT_OTHER_FUEL_PRICES["heating_oil_per_gallon"]
        )
        prices["propane_per_gallon"] = (
            propane if propane is not None else DEFAULT_OTHER_FUEL_PRICES["propane_per_gallon"]
        )

        # Seasonal electricity rate: derived discount unless supplied explicitly.
        prices["electricity_seasonal_per_kwh"] = round(
            electricity_per_kwh * self._seasonal_discount, 4
        )

        # Explicit overrides win over everything.
        prices.update(self._overrides)

        return PriceTable(region_key=region_key, prices=prices)
