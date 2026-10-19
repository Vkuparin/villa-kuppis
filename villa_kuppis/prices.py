"""Finnish day-ahead spot prices: bounds, fetch, validation, conversion.

Source: Elering's public day-ahead price API, bidding zone FI, no key:

    https://dashboard.elering.ee/api/nps/price?start=<date-time>&end=<date-time>

The response shape is::

    {"success": true, "data": {"fi": [{"timestamp": <unix s>,
                                       "price": <EUR/MWh>}, ...]}}

Rules implemented here (see docs/spot-price.md):

* "Tomorrow" is a calendar day in Europe/Helsinki.
* A day's bounds are the two separate local midnights, converted to UTC.
  Because of daylight saving a Helsinki day lasts 24 h normally, 23 h on
  the spring change and 25 h on the autumn change, i.e. 96, 92 or 100
  15-minute intervals.
* Internally the day is the half-open interval ``[start, end)``. Per the
  official OpenAPI spec the API ``start``/``end`` parameters are UTC
  date-time strings; the ``end`` parameter is the last second before the
  next midnight.
* Interval starts are stored as UTC unix seconds.
* Prices arrive raw in EUR/MWh without VAT; the app works in c/kWh, i.e.
  ``raw / 10``. Negative prices are valid.
* There is no zero fill: a day is only accepted when all expected
  intervals are present, on the 900 s grid, inside the bounds, with finite
  numeric prices.

The HTTP transport is injectable (``Callable[[str], bytes]``) so the whole
flow is testable offline.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")
API_URL = "https://dashboard.elering.ee/api/nps/price"
INTERVAL_SECONDS = 900
DEFAULT_TIMEOUT_SECONDS = 30.0

# A transport maps a full URL to the raw response body.
Transport = Callable[[str], bytes]


class PriceFetchError(Exception):
    """The HTTP request failed (network, timeout, HTTP error)."""


class PriceValidationError(Exception):
    """The response was not a complete, valid day of prices."""


@dataclass(frozen=True)
class PriceInterval:
    """One 15-minute interval: UTC start and the price as published."""

    ts_utc: int
    raw_price_eur_mwh: Decimal

    @property
    def price_c_kwh(self) -> Decimal:
        """c/kWh: raw EUR/MWh divided by 10 (exact Decimal division)."""
        return self.raw_price_eur_mwh / Decimal(10)


def helsinki_day_bounds(day: date) -> tuple[int, int]:
    """Half-open ``[start, end)`` unix seconds for a Helsinki calendar day."""
    start = datetime(day.year, day.month, day.day, tzinfo=HELSINKI)
    next_day = day + timedelta(days=1)
    end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=HELSINKI)
    return int(start.timestamp()), int(end.timestamp())


def expected_interval_count(day: date) -> int:
    """96 normally, 92 on the spring DST change, 100 on the autumn one."""
    start, end = helsinki_day_bounds(day)
    return (end - start) // INTERVAL_SECONDS


def build_url(day: date) -> str:
    """Elering URL for one Helsinki day.

    Per the official OpenAPI spec (dashboard.elering.ee/v3/api-docs,
    GET /api/nps/price), ``start`` and ``end`` are UTC date-time strings
    (accepted formats include ``yyyy-MM-dd'T'hh:mm:ssZ``). ``start`` is the
    first second of the Helsinki day; ``end`` the last second before the
    next midnight (the internal end bound minus one). Response timestamps
    remain unix seconds.
    """
    start, end = helsinki_day_bounds(day)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    query = urlencode({
        "start": datetime.fromtimestamp(start, tz=timezone.utc).strftime(fmt),
        "end": datetime.fromtimestamp(end - 1, tz=timezone.utc).strftime(fmt),
    })
    return f"{API_URL}?{query}"


def default_transport(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """stdlib urllib transport; raises on HTTP/network errors."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def to_decimal_price(value: object) -> Decimal:
    """Parse one API price into a finite Decimal EUR/MWh.

    Rejects booleans, strings, missing values and non-finite numbers.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PriceValidationError(
            f"price must be a number, got {type(value).__name__}: {value!r}"
        )
    try:
        price = Decimal(str(value))
    except InvalidOperation as exc:
        raise PriceValidationError(f"malformed price {value!r}") from exc
    if not price.is_finite():
        raise PriceValidationError(f"price is not finite: {value!r}")
    return price


def fetch_day(day: date, transport: Transport | None = None,
              timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[PriceInterval]:
    """Fetch and fully validate one Helsinki day.

    Any failure (HTTP error, timeout, bad JSON, invalid day) raises
    ``PriceFetchError``/``PriceValidationError`` and returns nothing, so a
    caller can decide to keep whatever it had before.
    """
    if transport is None:
        transport = lambda url: default_transport(url, timeout)
    try:
        payload = transport(build_url(day))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise PriceFetchError(f"fetch failed: {exc}") from exc
    return validate_day(payload, day)


def validate_day(payload: bytes, day: date) -> list[PriceInterval]:
    """Validate an Elering response body against one Helsinki day.

    Requires ``success`` true and a ``data.fi`` list that covers exactly the
    expected intervals: sorted-contiguous on the 900 s grid, no duplicates,
    nothing outside ``[start, end)``, finite numeric prices. Returns the
    intervals sorted by UTC start.
    """
    start, end = helsinki_day_bounds(day)
    expected = {start + i * INTERVAL_SECONDS
                for i in range((end - start) // INTERVAL_SECONDS)}

    try:
        body = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PriceValidationError(f"response is not valid JSON: {exc}") from exc

    if not isinstance(body, dict) or body.get("success") is not True:
        raise PriceValidationError("response is missing success=true")
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("fi"), list):
        raise PriceValidationError("response is missing the data.fi list")

    seen: dict[int, Decimal] = {}
    for entry in data["fi"]:
        if not isinstance(entry, dict):
            raise PriceValidationError(f"fi entry is not an object: {entry!r}")
        ts = entry.get("timestamp")
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise PriceValidationError(
                f"timestamp must be unix seconds, got {type(ts).__name__}: {ts!r}"
            )
        if ts not in expected:
            where = "outside the requested day" if not start <= ts < end \
                else "not on the 900 s grid"
            raise PriceValidationError(f"unexpected timestamp {ts} ({where})")
        if ts in seen:
            raise PriceValidationError(f"duplicate timestamp {ts}")
        seen[ts] = to_decimal_price(entry.get("price"))

    for ts in sorted(expected - seen.keys()):
        raise PriceValidationError(
            f"missing interval at {ts} (UTC); no zero fill is done"
        )

    return [PriceInterval(ts, seen[ts]) for ts in sorted(seen)]
