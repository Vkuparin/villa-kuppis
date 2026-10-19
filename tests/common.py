"""Shared helpers for the offline test suite (mocked transport, temp dirs)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from villa_kuppis.prices import (INTERVAL_SECONDS, HELSINKI,
                                 helsinki_day_bounds)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

NORMAL_DAY = date(2026, 10, 20)   # 24 h day, 96 intervals
SPRING_DAY = date(2026, 3, 29)    # 23 h day, 92 intervals
AUTUMN_DAY = date(2026, 10, 25)   # 25 h day, 100 intervals


def elering_payload(day: date, prices: list | None = None, *,
                    omit: str | None = None, drop: set | frozenset = frozenset(),
                    extra: list = []) -> bytes:
    """Build an Elering-format response body for one Helsinki day.

    ``prices`` is a list of raw EUR/MWh values in interval order (default:
    1.0 everywhere). ``omit`` breaks the body on purpose: 'success',
    'data', 'fi' or 'fi_empty'. ``drop`` removes intervals by index,
    ``extra`` appends (ts, price) pairs.
    """
    start, end = helsinki_day_bounds(day)
    count = (end - start) // INTERVAL_SECONDS
    if prices is None:
        prices = [1.0] * count
    fi = []
    for i in range(count):
        if i in drop:
            continue
        fi.append({"timestamp": start + i * INTERVAL_SECONDS,
                   "price": prices[i]})
    fi.extend({"timestamp": ts, "price": price} for ts, price in extra)
    body: dict = {"success": True, "data": {"fi": fi}}
    if omit == "success":
        body.pop("success")
    elif omit == "data":
        body["data"] = None
    elif omit == "fi":
        body["data"] = {}
    elif omit == "fi_empty":
        body["data"] = {"fi": []}
    return json.dumps(body).encode()


class FakeTransport:
    """Scripted HTTP transport: pops responses (bytes or Exception)."""

    def __init__(self, *responses: object, default=None):
        self.responses = list(responses)
        self.default = default
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if self.responses:
            item = self.responses.pop(0)
        elif self.default is not None:
            item = self.default(url)
        else:
            raise AssertionError(
                f"FakeTransport called {len(self.urls)} times; only "
                f"{len(self.responses) + (1 if self.default else 0)} "
                "responses were scripted"
            )
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, bytes)
        return item


class TempServiceTest(unittest.TestCase):
    """Base case: temp data dir, injected clock, per-test service."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="villa-kuppis-test-")
        self.data_dir = Path(self._tmp.name)
        self.clock = [datetime(2026, 10, 19, 11, 0, 0, tzinfo=HELSINKI)]
        self.service = None

    def make_service(self, transport=None):
        from villa_kuppis.service import SpotPriceService
        self.service = SpotPriceService(self.data_dir,
                                        transport=transport,
                                        now=lambda: self.clock[0])
        return self.service

    def tearDown(self) -> None:
        if self.service is not None:
            self.service.close()
        self._tmp.cleanup()
