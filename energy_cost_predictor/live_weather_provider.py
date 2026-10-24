"""Live weather provider backed by the Open-Meteo API.

Open-Meteo (https://open-meteo.com) is a free weather service that requires no
API key. This provider implements the ``WeatherProvider`` Protocol shape
(:mod:`energy_cost_predictor.providers`) by calling Open-Meteo's archive /
forecast endpoints and mapping the daily mean temperature onto the package's
:class:`~energy_cost_predictor.models.WeatherInterval` daily shape.

Design notes
------------
- Weather is the dominant driver of the heating-demand model, so accurate daily
  temperatures matter more than any other live input. Daily ``temperature_2m_mean``
  is requested (hourly is unnecessary for this model; monthly would be too
  coarse to capture cold snaps).
- A region key is resolved to a latitude/longitude. Massachusetts ("MA") maps to
  Boston. Add more regions to ``REGION_COORDS`` as needed.
- Any day the API omits (or returns null for) is emitted as a
  :class:`WeatherInterval` with ``outdoor_temp_f=None`` so the demand model's
  missing-interval handling (Req 4.6) stays exercised rather than silently
  dropping days.
- Uses only the standard library (``urllib``) so no third-party HTTP dependency
  is introduced. Network/parse failures raise so the provider adapter's guards
  surface them; this provider never fabricates data.

This module performs network I/O and therefore lives outside the pure core. Wrap
it in :class:`~energy_cost_predictor.provider_adapter.WeatherProviderAdapter` (as
the ``Predictor`` does) to get the timeout and field guards.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .models import WeatherInterval, WeatherSeries

__all__ = ["OpenMeteoWeatherProvider", "REGION_COORDS"]


#: Maps a region key to an approximate representative latitude/longitude. The
#: model uses one point per region; Massachusetts is anchored on Boston.
REGION_COORDS: Dict[str, Tuple[float, float]] = {
    "MA": (42.3601, -71.0589),  # Boston, MA
}

#: Open-Meteo historical archive endpoint (reanalysis, ~1940-present).
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

#: Open-Meteo forecast endpoint (recent past + up to ~16 days ahead).
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class OpenMeteoWeatherProvider:
    """Daily outdoor temperatures from Open-Meteo (Req 2.1, 2.4).

    :param region_coords: Optional override of the region-key -> (lat, lon) map.
    :param timeout_seconds: Per-request network timeout. Note the Predictor's
        adapter also enforces its own deadline; this is the lower-level socket
        timeout.
    """

    def __init__(
        self,
        region_coords: Optional[Dict[str, Tuple[float, float]]] = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._coords = dict(region_coords) if region_coords else dict(REGION_COORDS)
        self._timeout_seconds = timeout_seconds

    def get_temperatures(
        self, region_key: str, start: date, end: date
    ) -> WeatherSeries:
        """Return daily mean outdoor temperatures (deg F) for [start, end].

        Chooses the archive endpoint for past ranges and the forecast endpoint
        when the range extends to/beyond today, so both historical estimates and
        upcoming-season forecasts are supported. Missing days are returned as
        intervals with ``outdoor_temp_f=None``.
        """
        if region_key not in self._coords:
            raise KeyError(
                f"No coordinates configured for region {region_key!r}; "
                f"add it to REGION_COORDS."
            )
        lat, lon = self._coords[region_key]

        endpoint = _FORECAST_URL if end >= date.today() else _ARCHIVE_URL
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "temperature_2m_mean",
            "temperature_unit": "fahrenheit",
            "timezone": "America/New_York",
        }
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(url, headers={"User-Agent": "energy-cost-predictor"})
        with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        daily = payload.get("daily") or {}
        days = daily.get("time") or []
        temps = daily.get("temperature_2m_mean") or []

        by_date: Dict[date, Optional[float]] = {}
        for day_str, temp in zip(days, temps):
            d = datetime.strptime(day_str, "%Y-%m-%d").date()
            by_date[d] = float(temp) if temp is not None else None

        # Emit one interval per calendar day in the range so gaps are explicit.
        intervals: List[WeatherInterval] = []
        current = start
        while current <= end:
            intervals.append(
                WeatherInterval(
                    interval_date=current,
                    outdoor_temp_f=by_date.get(current),
                )
            )
            current = current + timedelta(days=1)

        return WeatherSeries(region_key=region_key, intervals=intervals)
