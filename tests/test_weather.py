"""Tests for the FMI open data client (weather.py, card F3.3).

No network: parse tests use a reduced copy of a real FMI
``omso:PointTimeSeriesObservation`` response (same namespaces and layout
as the live service), and the fetch tests serve the fixture from a local
HTTP server.
"""

from __future__ import annotations

import http.server
import threading
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from villa_kuppis.weather import (
    WeatherError,
    fetch_forecast_temperature,
    fetch_hourly_temperature,
    nearest_station,
    parse_coord_spec,
    parse_timevaluepair,
    weather_sites_from_env,
)

# Two stations, in the live layout. Member A carries the hourly mean
# (TA_PT1H_AVG, twice, one value 'missing') with the full target:Location
# name triple (display name, geoid, WMO); member B is the same station's
# hourly MAX parameter and must be filtered out; member C is a second,
# further station with one valid value.
_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection numberMatched="3" numberOfFeatures="3"
    xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:omso="http://inspire.ec.europa.eu/schemas/omso/3.0"
    xmlns:om="http://www.opengis.net/om/2.0"
    xmlns:sams="http://www.opengis.net/samplingSpatial/2.0"
    xmlns:sam="http://www.opengis.net/sampling/2.0"
    xmlns:target="http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1"
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:wml2="http://www.opengis.net/waterml/2.0"
    xmlns:xlink="http://www.w3.org/1999/xlink">
  <wfs:member>
    <omso:PointTimeSeriesObservation gml:id="obs-100971">
      <om:observedProperty
          xlink:href="https://opendata.fmi.fi/meta?observableProperty=observation&amp;param=TA_PT1H_AVG&amp;language=eng"/>
      <om:featureOfInterest>
        <sams:SF_SpatialSamplingFeature gml:id="fi-100971">
          <sam:sampledFeature>
            <target:LocationCollection gml:id="lc-100971">
              <target:member>
                <target:Location gml:id="loc-100971">
                  <gml:identifier codeSpace="http://xml.fmi.fi/namespace/stationcode/fmisid">100971</gml:identifier>
                  <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">Helsinki Kaisaniemi</gml:name>
                  <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/geoid">-16000042</gml:name>
                  <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/wmo">2974</gml:name>
                </target:Location>
              </target:member>
            </target:LocationCollection>
          </sam:sampledFeature>
          <sams:shape>
            <gml:Point gml:id="pt-100971" srsName="http://www.opengis.net/def/crs/EPSG/0/4258" srsDimension="2">
              <gml:name>Helsinki Kaisaniemi</gml:name>
              <gml:pos>60.17523 24.94459 </gml:pos>
            </gml:Point>
          </sams:shape>
        </sams:SF_SpatialSamplingFeature>
      </om:featureOfInterest>
      <om:result>
        <wml2:MeasurementTimeseries gml:id="res-100971">
          <wml2:point>
            <wml2:MeasurementTVP>
              <wml2:time>2026-10-19T05:00:00Z</wml2:time>
              <wml2:value>2.4</wml2:value>
            </wml2:MeasurementTVP>
          </wml2:point>
          <wml2:point>
            <wml2:MeasurementTVP>
              <wml2:time>2026-10-19T06:00:00Z</wml2:time>
              <wml2:value>missing</wml2:value>
            </wml2:MeasurementTVP>
          </wml2:point>
        </wml2:MeasurementTimeseries>
      </om:result>
    </omso:PointTimeSeriesObservation>
  </wfs:member>
  <wfs:member>
    <omso:PointTimeSeriesObservation gml:id="obs-100971-max">
      <om:observedProperty
          xlink:href="https://opendata.fmi.fi/meta?observableProperty=observation&amp;param=TA_PT1H_MAX&amp;language=eng"/>
      <om:featureOfInterest>
        <sams:SF_SpatialSamplingFeature gml:id="fi-100971-max">
          <sams:shape>
            <gml:Point gml:id="pt-100971-max" srsName="http://www.opengis.net/def/crs/EPSG/0/4258" srsDimension="2">
              <gml:pos>60.17523 24.94459 </gml:pos>
            </gml:Point>
          </sams:shape>
        </sams:SF_SpatialSamplingFeature>
      </om:featureOfInterest>
      <om:result>
        <wml2:MeasurementTimeseries gml:id="res-100971-max">
          <wml2:point>
            <wml2:MeasurementTVP>
              <wml2:time>2026-10-19T05:00:00Z</wml2:time>
              <wml2:value>5.1</wml2:value>
            </wml2:MeasurementTVP>
          </wml2:point>
        </wml2:MeasurementTimeseries>
      </om:result>
    </omso:PointTimeSeriesObservation>
  </wfs:member>
  <wfs:member>
    <omso:PointTimeSeriesObservation gml:id="obs-100752">
      <om:observedProperty
          xlink:href="https://opendata.fmi.fi/meta?observableProperty=observation&amp;param=TA_PT1H_AVG&amp;language=eng"/>
      <om:featureOfInterest>
        <sams:SF_SpatialSamplingFeature gml:id="fi-100752">
          <sam:sampledFeature>
            <target:LocationCollection gml:id="lc-100752">
              <target:member>
                <target:Location gml:id="loc-100752">
                  <gml:identifier codeSpace="http://xml.fmi.fi/namespace/stationcode/fmisid">100752</gml:identifier>
                  <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">Vantaa</gml:name>
                </target:Location>
              </target:member>
            </target:LocationCollection>
          </sam:sampledFeature>
          <sams:shape>
            <gml:Point gml:id="pt-100752" srsName="http://www.opengis.net/def/crs/EPSG/0/4258" srsDimension="2">
              <gml:name>Vantaa</gml:name>
              <gml:pos>60.30726 24.92961 </gml:pos>
            </gml:Point>
          </sams:shape>
        </sams:SF_SpatialSamplingFeature>
      </om:featureOfInterest>
      <om:result>
        <wml2:MeasurementTimeseries gml:id="res-100752">
          <wml2:point>
            <wml2:MeasurementTVP>
              <wml2:time>2026-10-19T05:00:00Z</wml2:time>
              <wml2:value>3.0</wml2:value>
            </wml2:MeasurementTVP>
          </wml2:point>
        </wml2:MeasurementTimeseries>
      </om:result>
    </omso:PointTimeSeriesObservation>
  </wfs:member>
</wfs:FeatureCollection>
"""


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    body = _FIXTURE

    def do_GET(self):
        # Remember the request before answering, so the client never sees
        # the response first.
        type(self).last_request = self.path
        body = self.body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ServerCase(unittest.TestCase):
    """Base: one local HTTP server per test."""

    def start_server(self, handler):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return "http://127.0.0.1:%d/wfs" % server.server_address[1]

    def tearDown(self):
        server = getattr(self, "_server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
            self._thread.join(timeout=5)


class ParseTimevaluepairTest(unittest.TestCase):
    def test_keeps_mean_station_only(self):
        series = parse_timevaluepair(_FIXTURE)
        # The TA_PT1H_MAX member is filtered out.
        self.assertEqual(len(series), 2)
        kaisaniemi = series[0]
        self.assertEqual(kaisaniemi.id, "100971")
        # Display name picked by codeSpace, not the geoid/WMO siblings.
        self.assertEqual(kaisaniemi.name, "Helsinki Kaisaniemi")
        # gml:pos carries trailing whitespace; it must be stripped.
        self.assertAlmostEqual(kaisaniemi.lat, 60.17523, places=5)
        self.assertAlmostEqual(kaisaniemi.lon, 24.94459, places=5)
        # 'missing' values are dropped, timestamps are UTC-aware.
        self.assertEqual(
            kaisaniemi.values,
            ((datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc), 2.4),),
        )
        vantaa = series[1]
        self.assertEqual(vantaa.name, "Vantaa")
        self.assertAlmostEqual(vantaa.values[0][1], 3.0)

    def test_invalid_xml_raises(self):
        with self.assertRaises(WeatherError) as ctx:
            parse_timevaluepair("<wfs:FeatureCollection><wfs:member>")
        self.assertIn("not valid XML", str(ctx.exception))

    def test_nonfinite_measurement_is_a_gap(self):
        series = parse_timevaluepair(_FIXTURE.replace("2.4", "NaN"))
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].name, "Vantaa")

    def test_empty_collection_is_empty(self):
        empty = (
            '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0">'
            "</wfs:FeatureCollection>"
        )
        self.assertEqual(parse_timevaluepair(empty), [])

    def test_nearest_station_picks_closest(self):
        series = parse_timevaluepair(_FIXTURE)
        nearest = nearest_station(series, 60.17, 24.95)  # point near the public Helsinki station
        self.assertEqual(nearest.name, "Helsinki Kaisaniemi")
        self.assertLess(
            series[0].distance_km(60.17, 24.95),
            series[1].distance_km(60.17, 24.95),
        )

    def test_nearest_station_empty_raises(self):
        with self.assertRaises(WeatherError) as ctx:
            nearest_station([], 60.0, 25.0)
        self.assertIn("no observation stations", str(ctx.exception))


class FetchHourlyTemperatureTest(_ServerCase):
    def test_offset_timestamps_are_converted_to_utc(self):
        base = self.start_server(_FixtureHandler)
        start = datetime(2026, 10, 19, 8, 17, tzinfo=timezone(timedelta(hours=3)))
        fetch_hourly_temperature(60.17, 24.95, start, start + timedelta(hours=1), base_url=base)
        request = unquote(_FixtureHandler.last_request)
        self.assertIn("starttime=2026-10-19T05:00:00Z", request)
        self.assertIn("endtime=2026-10-19T06:00:00Z", request)

    def test_uses_stored_query_and_returns_nearest(self):
        base = self.start_server(_FixtureHandler)
        start = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=1)
        result = fetch_hourly_temperature(
            60.17, 24.95, start, end, base_url=base)
        self.assertEqual(result.name, "Helsinki Kaisaniemi")
        self.assertEqual(len(result.values), 1)
        request = unquote(_FixtureHandler.last_request)
        self.assertIn(
            "storedquery_id=fmi::observations::weather::hourly::timevaluepair",
            request)
        self.assertIn("parameters=TA_PT1H_AVG", request)
        self.assertIn("starttime=2026-10-19T05:00:00Z", request)
        self.assertIn("endtime=2026-10-19T06:00:00Z", request)
        # bbox is lon,lat,lon,lat around the point, six decimals,
        # default half-width 0.45 deg (BBOX_MARGIN_DEG).
        self.assertIn(
            "bbox=24.500000,59.720000,25.400000,60.620000", request)

    def test_http_error_raises(self):
        class FailHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                body = b"<wfs:ExceptionReport><Exception>Bad request</Exception></wfs:ExceptionReport>"
                self.send_response(400)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        base = self.start_server(FailHandler)
        start = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)
        with self.assertRaises(WeatherError) as ctx:
            fetch_hourly_temperature(
                60.18, 25.07, start, start + timedelta(hours=1),
                base_url=base)
        self.assertIn("HTTP 400", str(ctx.exception))

    def test_no_stations_raises(self):
        class EmptyHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                body = (
                    b'<wfs:FeatureCollection numberMatched="0" numberOfFeatures="0" '
                    b'xmlns:wfs="http://www.opengis.net/wfs/2.0"></wfs:FeatureCollection>'
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        base = self.start_server(EmptyHandler)
        start = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)
        with self.assertRaises(WeatherError) as ctx:
            fetch_hourly_temperature(
                60.18, 25.07, start, start + timedelta(hours=1),
                base_url=base)
        self.assertIn("no observation stations", str(ctx.exception))

    def test_unreachable_host_raises(self):
        start = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)
        with self.assertRaises(WeatherError) as ctx:
            fetch_hourly_temperature(
                60.18, 25.07, start, start + timedelta(hours=1),
                base_url="http://127.0.0.1:1/wfs", timeout=2.0)
        self.assertIn("could not reach FMI", str(ctx.exception))


class CoordSpecTest(unittest.TestCase):
    def test_parse_valid(self):
        self.assertEqual(parse_coord_spec("60.17,24.95"),
                         (60.17, 24.95))
        self.assertEqual(parse_coord_spec(" -12.5 , -130 "), (-12.5, -130.0))

    def test_parse_invalid(self):
        for bad in ("60.18", "abc,25", "91,25", "60,181", "", "60,25,1"):
            with self.assertRaises(ValueError, msg=bad):
                parse_coord_spec(bad)


class WeatherSitesFromEnvTest(unittest.TestCase):
    def test_both_sites(self):
        sites = weather_sites_from_env({
            "VILLA_KUPPIS_WEATHER_HOME": "60.17,24.95",
            "VILLA_KUPPIS_WEATHER_COTTAGE": " 61.0,25.0 ",
        })
        self.assertEqual(sites, {
            "home": (60.17, 24.95),
            "cottage": (61.0, 25.0),
        })

    def test_empty(self):
        self.assertEqual(weather_sites_from_env({}), {})

    def test_bad_value_raises(self):
        with self.assertRaises(WeatherError) as ctx:
            weather_sites_from_env(
                {"VILLA_KUPPIS_WEATHER_HOME": "helsinki"})
        self.assertIn("VILLA_KUPPIS_WEATHER_HOME", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


# A reduced point forecast in the timevaluepair layout (card F5.1): one
# member for the requested point, parameter Temperature.
_FORECAST = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection
    xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:omso="http://inspire.ec.europa.eu/schemas/omso/3.0"
    xmlns:om="http://www.opengis.net/om/2.0"
    xmlns:sams="http://www.opengis.net/samplingSpatial/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:wml2="http://www.opengis.net/waterml/2.0"
    xmlns:xlink="http://www.w3.org/1999/xlink">
  <wfs:member>
    <omso:PointTimeSeriesObservation gml:id="fc-1">
      <om:observedProperty
          xlink:href="https://opendata.fmi.fi/meta?observableProperty=forecast&amp;param=Temperature&amp;language=eng"/>
      <om:featureOfInterest>
        <sams:SF_SpatialSamplingFeature gml:id="fi-fc-1">
          <sams:shape>
            <gml:Point gml:id="pt-fc-1" srsDimension="2">
              <gml:name>60.17,24.95</gml:name>
              <gml:pos>60.17000 24.95000 </gml:pos>
            </gml:Point>
          </sams:shape>
        </sams:SF_SpatialSamplingFeature>
      </om:featureOfInterest>
      <om:result>
        <wml2:MeasurementTimeseries gml:id="res-fc-1">
          <wml2:point><wml2:MeasurementTVP>
            <wml2:time>2026-10-19T13:00:00Z</wml2:time>
            <wml2:value>7.5</wml2:value>
          </wml2:MeasurementTVP></wml2:point>
          <wml2:point><wml2:MeasurementTVP>
            <wml2:time>2026-10-19T14:00:00Z</wml2:time>
            <wml2:value>6.9</wml2:value>
          </wml2:MeasurementTVP></wml2:point>
        </wml2:MeasurementTimeseries>
      </om:result>
    </omso:PointTimeSeriesObservation>
  </wfs:member>
</wfs:FeatureCollection>
"""


class _ForecastHandler(_FixtureHandler):
    body = _FORECAST


class FetchForecastTest(_ServerCase):
    def test_point_forecast_query_and_values(self):
        base = self.start_server(_ForecastHandler)
        start = datetime(2026, 10, 19, 12, 40, tzinfo=timezone.utc)
        result = fetch_forecast_temperature(60.17, 24.95, start,
                                            start + timedelta(hours=48),
                                            base_url=base)
        self.assertEqual([v for _t, v in result.values], [7.5, 6.9])
        request = unquote(_ForecastHandler.last_request)
        self.assertIn("storedquery_id=fmi::forecast::edited::weather::"
                      "scandinavia::point::timevaluepair", request)
        self.assertIn("latlon=60.1700,24.9500", request)
        self.assertIn("parameters=Temperature", request)
        self.assertIn("starttime=2026-10-19T12:00:00Z", request)

    def test_observation_parser_ignores_forecast_members(self):
        self.assertEqual(parse_timevaluepair(_FORECAST), [])
