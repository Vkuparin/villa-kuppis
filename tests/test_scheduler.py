"""Scheduler: 14:25 gate, attempt records, restarts, manual retry."""

import urllib.error
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from common import HELSINKI, FakeTransport, TempServiceTest, elering_payload


def helsinki_day_at(now: datetime, hh: int, mm: int, ss: int = 0) -> datetime:
    d = now.astimezone(HELSINKI)
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=HELSINKI)


class TestScheduler(TempServiceTest):
    def _tomorrow(self):
        return self.clock[0].astimezone(HELSINKI).date() + timedelta(days=1)

    def _set_time(self, hh: int, mm: int, ss: int = 0) -> None:
        self.clock[0] = helsinki_day_at(self.clock[0], hh, mm, ss)

    def test_before_1425_no_attempt(self):
        transport = FakeTransport(elering_payload(self._tomorrow()))
        service = self.make_service(transport)
        self._set_time(14, 24, 59)
        outcome = service.schedule_once()
        assert outcome.status == "not_yet"
        assert transport.urls == [], "no HTTP before 14:25"
        assert service.store.attempt("2026-10-19") is None

    def test_at_1425_attempts_tomorrow(self):
        tomorrow = self._tomorrow()
        transport = FakeTransport(elering_payload(tomorrow))
        service = self.make_service(transport)
        self._set_time(14, 25)
        outcome = service.schedule_once()
        assert outcome.status == "ok"
        assert len(transport.urls) == 1
        # The fetched start parameter must be tomorrow in Helsinki time.
        # 2026-10-20 Helsinki = [2026-10-19T21:00:00Z, 2026-10-20T21:00:00Z)
        qs = parse_qs(urlparse(transport.urls[0]).query)
        assert qs["start"] == ["2026-10-19T21:00:00Z"]
        assert qs["end"] == ["2026-10-20T20:59:59Z"]
        assert datetime.fromisoformat(
            qs["start"][0].replace("Z", "+00:00")
        ).astimezone(HELSINKI).date() == tomorrow
        attempt = service.store.attempt("2026-10-19")
        assert attempt is not None and attempt.outcome == "ok"
        assert service.stored_intervals(tomorrow) is not None

    def test_late_start_catches_up(self):
        tomorrow = self._tomorrow()
        transport = FakeTransport(elering_payload(tomorrow))
        service = self.make_service(transport)
        self._set_time(18, 0)  # started after 14:25: still must catch up
        outcome = service.schedule_once()
        assert outcome.status == "ok"
        assert len(transport.urls) == 1

    def test_no_second_attempt_on_the_same_day(self):
        tomorrow = self._tomorrow()
        transport = FakeTransport(elering_payload(tomorrow),
                                  elering_payload(tomorrow))
        service = self.make_service(transport)
        self._set_time(15, 0)
        assert service.schedule_once().status == "ok"
        self._set_time(19, 0)
        outcome = service.schedule_once()
        assert outcome.status == "already_ok"
        assert len(transport.urls) == 1, "same-day re-check must not fetch"

    def test_failure_recorded_with_no_auto_retry(self):
        tomorrow = self._tomorrow()
        transport = FakeTransport(urllib.error.URLError("boom"))
        service = self.make_service(transport)
        self._set_time(15, 0)
        outcome = service.schedule_once()
        assert outcome.status == "error"
        assert outcome.detail
        assert len(transport.urls) == 1
        attempt = service.store.attempt("2026-10-19")
        assert attempt is not None and attempt.outcome == "error"
        assert service.stored_intervals(tomorrow) is None
        # Same day, later: still not retried automatically.
        self._set_time(19, 0)
        outcome = service.schedule_once()
        assert outcome.status == "already_error"
        assert len(transport.urls) == 1

    def test_manual_refresh_after_failure_is_allowed(self):
        tomorrow = self._tomorrow()
        service = self.make_service(FakeTransport(urllib.error.URLError("boom")))
        self._set_time(15, 0)
        assert service.schedule_once().status == "error"
        # Manual refresh is independent of the scheduler state.
        manual = FakeTransport(elering_payload(tomorrow))
        service._transport = manual
        result = service.refresh()
        assert result.day == tomorrow
        assert len(manual.urls) == 1
        attempt = service.store.attempt("2026-10-19")
        assert attempt is not None and attempt.outcome == "error"

    def test_interrupted_attempt_visible_and_not_retried(self):
        tomorrow = self._tomorrow()
        service = self.make_service(FakeTransport())  # nothing scripted
        self._set_time(15, 0)
        # Simulate a crash mid-attempt: the date is marked, then the
        # process dies before the outcome is recorded.
        service.store.mark_attempt("2026-10-19", service.now())
        service.close()
        # "Restart" on the same data dir.
        transport = FakeTransport(elering_payload(tomorrow))
        service2 = self.make_service(transport)
        outcome = service2.schedule_once()
        assert outcome.status == "already_running"
        assert transport.urls == [], "interrupted attempt must not refetch"
        attempt = service2.store.attempt("2026-10-19")
        assert attempt is not None and attempt.outcome == "running"

    def test_next_day_is_a_new_attempt(self):
        day1 = self._tomorrow()            # 2026-10-20
        day2 = day1 + timedelta(days=1)    # 2026-10-21
        transport = FakeTransport(elering_payload(day1),
                                  elering_payload(day2))
        service = self.make_service(transport)
        self._set_time(15, 0)
        assert service.schedule_once().status == "ok"
        # Next local date: a fresh attempt for the new tomorrow.
        self._set_time(15, 0)
        self.clock[0] = helsinki_day_at(self.clock[0] + timedelta(days=1),
                                        15, 0)
        outcome = service.schedule_once()
        assert outcome.status == "ok"
        assert len(transport.urls) == 2
        qs = parse_qs(urlparse(transport.urls[1]).query)
        # 2026-10-21 Helsinki = [2026-10-20T21:00:00Z, 2026-10-21T21:00:00Z)
        assert qs["start"] == ["2026-10-20T21:00:00Z"]
        assert qs["end"] == ["2026-10-21T20:59:59Z"]
        assert datetime.fromisoformat(
            qs["start"][0].replace("Z", "+00:00")
        ).astimezone(HELSINKI).date() == day2
