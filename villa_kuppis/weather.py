"""FMI open data client for outdoor temperature (cards F3.3 and F5.1).

Standard library only (urllib + ElementTree): the pump service must stay
dependency-free, and FMI's open data service needs no key.

The service is an OGC WFS 2.0.0 endpoint (https://opendata.fmi.fi/wfs).
One stored query is used:

    fmi::observations::weather::hourly::timevaluepair

with a small bounding box around the house and the parameter code
``TA_PT1H_AVG`` (hourly mean air temperature, Celsius). Each
``wfs:member`` is an INSPIRE ``omso:PointTimeSeriesObservation``: the
station's ``gml:identifier`` (FMI station id) and its ``gml:name`` live
under ``target:Location`` (alongside geoid and WMO ``gml:name`` siblings,
so the display name is picked by codeSpace), the position is the
``sams:shape`` ``gml:pos`` in EPSG:4258 (latitude then longitude, and the
service pads it with trailing whitespace) and the values are
``wml2:MeasurementTVP`` pairs in WaterML 2.0.

A request covering several hours returns one member per station with all
hours in it, so a 24-hour backfill is a single request.

The forecast (card F5.1) uses the point query

    fmi::forecast::edited::weather::scandinavia::point::timevaluepair

with ``latlon`` and the parameter ``Temperature``. Its answer has the same
timevaluepair layout, with one member for the requested point.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Mapping

WFS_BASE = "https://opendata.fmi.fi/wfs"
STORED_QUERY_ID = "fmi::observations::weather::hourly::timevaluepair"
TEMP_PARAM = "TA_PT1H_AVG"  # hourly mean air temperature, Celsius
FORECAST_QUERY_ID = "fmi::forecast::edited::weather::scandinavia::point::timevaluepair"
FORECAST_PARAM = "Temperature"  # forecast air temperature, Celsius
DEFAULT_TIMEOUT = 30.0
# About 50 km north/south, narrower east/west in Finland. Include nearby
# stations in rural areas; choose the closest one from the returned data.
BBOX_MARGIN_DEG = 0.45

_NS_WFS = "http://www.opengis.net/wfs/2.0"
_NS_GML = "http://www.opengis.net/gml/3.2"
_NS_OM = "http://www.opengis.net/om/2.0"
_NS_WML2 = "http://www.opengis.net/waterml/2.0"
_NS_TARGET = "http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1"
_NS_XLINK = "http://www.w3.org/1999/xlink"

USER_AGENT = "villa-kuppis/0.2 (home pump app; historical outdoor data)"


class WeatherError(Exception):
    """FMI could not be reached or the answer was unusable."""


@dataclass(frozen=True)
class StationSeries:
    """Hourly mean air temperatures from one FMI weather station."""

    id: str
    name: str
    lat: float
    lon: float
    values: tuple[tuple[datetime, float], ...]

    def distance_km(self, lat: float, lon: float) -> float:
        """Haversine distance from the station to a point."""
        phi1, phi2 = radians(lat), radians(self.lat)
        dphi = phi2 - phi1
        dlmb = radians(lon - self.lon)
        a = (sin(dphi / 2) ** 2
             + cos(phi1) * cos(phi2) * sin(dlmb / 2) ** 2)
        return 2 * 6371.0 * asin(sqrt(a))


def parse_coord_spec(spec: str) -> tuple[float, float]:
    """Parse ``"60.17,24.95"`` into (lat, lon).

    Raises ValueError on anything that is not a valid point.
    """
    parts = spec.split(",")
    if len(parts) != 2:
        raise ValueError(f"expected 'lat,lon', got {spec!r}")
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(f"expected 'lat,lon', got {spec!r}") from None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"coordinates out of range: {spec!r}")
    return lat, lon


def weather_sites_from_env(environ: Mapping[str, str] | None = None) -> dict[str, tuple[float, float]]:
    """Configured observation points from the environment.

    ``VILLA_KUPPIS_WEATHER_HOME`` and ``VILLA_KUPPIS_WEATHER_COTTAGE`` hold
    ``"lat,lon"`` strings; unset variables are skipped. A malformed value
    raises WeatherError so a typo is noticed at startup, not silently
    dropped.
    """
    environ = os.environ if environ is None else environ
    sites: dict[str, tuple[float, float]] = {}
    for key, name in (("VILLA_KUPPIS_WEATHER_HOME", "home"),
                      ("VILLA_KUPPIS_WEATHER_COTTAGE", "cottage")):
        raw = (environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            sites[name] = parse_coord_spec(raw)
        except ValueError as err:
            raise WeatherError(f"bad coordinates in {key}: {err}") from None
    return sites


def _parse_ts(text: str) -> datetime:
    """FMI sends ISO 8601 UTC like 2026-10-19T05:00:00Z."""
    ts = datetime.fromisoformat(text.strip())
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _station_name(member) -> str:
    """Pick the display name of a station from its ``gml:name`` elements.

    ``target:Location`` carries several ``gml:name`` siblings: the display
    name, a geoid and a WMO code. The display name is recognised by its
    codeSpace; if none matches, the first non-numeric name is used.
    """
    names = list(member.iter(f"{{{_NS_GML}}}name"))
    for el in names:
        text = (el.text or "").strip()
        code_space = el.get("codeSpace") or ""
        if text and (code_space.endswith("locationcode/name")
                     or code_space.endswith("station/identifier/name")):
            return text
    for el in names:
        text = (el.text or "").strip()
        if not text:
            continue
        try:
            float(text)
        except ValueError:
            return text
    return ""


def parse_timevaluepair(text: str, param: str = TEMP_PARAM) -> list[StationSeries]:
    """Parse a FMI timevaluepair FeatureCollection into station series.

    Only members carrying ``param`` (by default the hourly mean
    temperature) are kept;
    members without a usable position or without values are dropped.
    Raises WeatherError if the document is not parseable at all.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        raise WeatherError(f"the FMI response is not valid XML: {err}") from None

    series: list[StationSeries] = []
    for member in root.iter(f"{{{_NS_WFS}}}member"):
        # The parameter code travels in the observedProperty xlink:href
        # query string, e.g. ...?param=TA_PT1H_AVG&language=eng. The
        # element sits inside the member's PointTimeSeriesObservation, so
        # search descendants, not direct children.
        prop = member.find(f".//{{{_NS_OM}}}observedProperty")
        if prop is None:
            prop = member.find(f".//{{{_NS_TARGET}}}observedProperty")
        href = prop.get(f"{{{_NS_XLINK}}}href", "") if prop is not None else ""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        if param not in query.get("param", []):
            continue
        # Station id: the member's single gml:identifier (inside
        # target:Location in the live layout).
        identifier = (member.findtext(f".//{{{_NS_GML}}}identifier") or "").strip()
        name = _station_name(member)
        # Position: the sams:shape gml:pos; the service pads it with
        # trailing whitespace.
        pos = (member.findtext(f".//{{{_NS_GML}}}pos") or "").strip()
        values: list[tuple[datetime, float]] = []
        for tvp in member.iter(f"{{{_NS_WML2}}}MeasurementTVP"):
            t = (tvp.findtext(f"{{{_NS_WML2}}}time") or "").strip()
            v = (tvp.findtext(f"{{{_NS_WML2}}}value") or "").strip()
            if not t or not v or v.lower() == "missing":
                continue
            try:
                value = float(v)
                if isfinite(value):
                    values.append((_parse_ts(t), value))
            except ValueError:
                continue
        if not pos or not values:
            continue
        try:
            lat, lon = (float(p) for p in pos.split())
        except ValueError:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        series.append(StationSeries(identifier, name, lat, lon,
                                    tuple(sorted(values))))
    return series


def nearest_station(series: list[StationSeries], lat: float,
                    lon: float) -> StationSeries:
    """The series from the station closest to a point.

    Raises WeatherError when there is nothing at all.
    """
    if not series:
        raise WeatherError("FMI returned no observation stations in range")
    return min(series, key=lambda s: (s.distance_km(lat, lon), s.id))


def _get(base_url: str, query: dict, timeout: float) -> str:
    """One GET to the WFS endpoint; WeatherError on any failure."""
    url = base_url + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            body = err.read().decode("utf-8", "replace")
            detail = " ".join(body.split())[:200]
        except Exception:
            pass
        raise WeatherError(
            f"FMI returned HTTP {err.code}"
            + (f": {detail}" if detail else "")
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise WeatherError(f"could not reach FMI: {reason}") from None
    return text


def fetch_hourly_temperature(lat: float, lon: float, start: datetime,
                             end: datetime, *, base_url: str = WFS_BASE,
                             timeout: float = DEFAULT_TIMEOUT,
                             bbox_margin: float = BBOX_MARGIN_DEG
                             ) -> StationSeries:
    """Hourly mean air temperature around a point, from the closest station.

    One HTTP request, whatever the window length. start/end are timezone
    aware (naive is read as UTC). bbox_margin is the half-width of the
    square box (degrees of latitude) searched for stations. Raises
    WeatherError on network failure or an unusable response; the caller
    decides how to react.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    m = bbox_margin
    query = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": STORED_QUERY_ID,
        "bbox": f"{lon - m:.6f},{lat - m:.6f},{lon + m:.6f},{lat + m:.6f}",
        "starttime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parameters": TEMP_PARAM,
    }
    text = _get(base_url, query, timeout)
    series = parse_timevaluepair(text)
    return nearest_station(series, lat, lon)


def fetch_forecast_temperature(lat: float, lon: float, start: datetime,
                               end: datetime, *, base_url: str = WFS_BASE,
                               timeout: float = DEFAULT_TIMEOUT
                               ) -> StationSeries:
    """Hourly forecast air temperature at a point (card F5.1).

    Same contract as fetch_hourly_temperature: one request, timezone-aware
    (or UTC) start/end, WeatherError on failure. The series is the
    forecast point itself, not a station.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    query = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": FORECAST_QUERY_ID,
        "latlon": f"{lat:.4f},{lon:.4f}",
        "starttime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestep": "60",
        "parameters": FORECAST_PARAM,
    }
    series = parse_timevaluepair(_get(base_url, query, timeout), FORECAST_PARAM)
    if not series:
        raise WeatherError("FMI returned no forecast for the point")
    return nearest_station(series, lat, lon)
