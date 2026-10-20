"""Scheduler: today fetched when missing, 14:25 gate for tomorrow, attempt
records, 15-minute retry spacing, restarts, manual retry."""

import urllib.error
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from common import HELSINKI, FakeTransport, TempServiceTest, elering_payload
from villa_kuppis.prices import validate_day
from villa_kuppis.service import RETRY_SECONDS


def helsinki_day_at(now: datetime, hh: int, mm: int, ss: int = 0) -> datetime:
    d = now.astimezone(HELSINKI)
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=HELSINKI)


class TestScheduler(TempServiceTest):
    def _today(self):
        return self.clock[0].astimezone(HELSINKI).date()

    def _tomorrow(self):
        return self._today() + timedelta(days=1)

    def _set_time(self, hh: int, mm: int, ss: int = 0) -> None:
        self.clock[0] = helsinki_day_at(self.clock[0], hh, mm, ss)

    def _seed(self, service, day) -> None:
        """Store a complete day without a fetch."""
        service.store.replace_day(
            day.isoformat(), validate_day(elering_payload(day), day),
            self.clock[0])

    def test_before_1425_with_today_stored_no_attempt(self):
        transport = FakeTransport(elering_payload(self._tomorrow()))
        service = self.make_service(transport)
        self._seed(service, self._today())
        self._set_time(14, 24, 59)
        outcome = service.schedule_once()
        assert outcome.status == "not_yet"
        assert transport.urls == [], "no tomorrow fetch before 14:25"
        assert service.store.attempt("2026-10-20") is None

    def test_missing_today_is_fetched_at_any_time(self):
        today = self._today()
        transport = FakeTransport(elering_payload(today))
        service = self.make_service(transport)
        self._set_time(6, 0)
        outcome = service.schedule_once()
        assert outcome.status == "ok"
        assert outcome.target_day == today
        assert len(transport.urls) == 1
        # 2026-10-19 Helsinki = [2026-10-18T21:00:00Z, 2026-10-19T21:00:00Z)
        qs = parse_qs(urlparse(transport.urls[0]).query)
        assert qs["start"] == ["2026-10-18T21:00:00Z"]
        assert service.stored_intervals(today) is not None
        attempt = service.store.attempt("2026-10-19")
        assert attempt is not None and attempt.outcome == "ok"
        # Before 14:25 nothing more is fetched.
        assert service.schedule_once().status == "not_yet"
        assert len(transport.urls) == 1

    def test_at_1425_attempts_tomorrow(self):
        tomorrow = self._tomorrow()
        transport = FakeTransport(elering_payload(tomorrow))
        service = self.make_service(transport)
        self._seed(service, self._today())
        self._set_time(14, 25)
        outcome = service.schedule_once()
        assert outcome.status == "ok"
        assert outcome.target_day == tomorrow
        assert len(transport.urls) == 1
        # The fetched start parameter must be tomorrow in Helsinki time.
        # 2026-10-20 Helsinki = [2026-10-19T21:00:00Z, 2026-10-20T21:00:00Z)
        qs = parse_qs(urlparse(transport.urls[0]).query)
        assert qs["start"] == ["2026-10-19T21:00:00Z"]
        assert qs["end"] == ["2026-10-20T20:59:59Z"]
        attempt = service.store.attempt("2026-10-20")
        assert attempt is not None and attempt.outcome == "ok"
        assert service.stored_intervals(tomorrow) is not None

    def test_late_start_catches_up_both_days(self):
        today, tomorrow = self._today(), self._tomorrow()
        transport = FakeTransport(elering_payload(today),
                                  elering_payload(tomorrow))
        service = self.make_service(transport)
        self._set_time(18, 0)  # started after 14:25 with nothing stored
        assert service.schedule_once().target_day == today
        assert service.schedule_once().target_day == tomorrow
        assert len(transport.urls) == 2
        assert service.schedule_once().status == "stored"
        assert len(transport.urls) == 2

    def test_stored_days_are_never_refetched(self):
        transport = FakeTransport(elering_payload(self._tomorrow()))
        service = self.make_service(transport)
        self._seed(service, self._today())
        self._seed(service, self._tomorrow())
        self._set_time(15, 0)
        assert service.schedule_once().status == "stored"
        self._set_time(23, 0)
        assert service.schedule_once().status == "stored"
        assert transport.urls == []

    def test_failure_is_not_retried_before_15_minutes(self):
        tomorrow = self._tomorrow()
        transport = FakeTransport(urllib.error.URLError("boom"))
        service = self.make_service(transport)
        self._seed(service, self._today())
        self._set_time(15, 0)
        outcome = service.schedule_once()
        assert outcome.status == "error"
        assert outcome.detail
        assert len(transport.urls) == 1
        attempt = service.store.attempt("2026-10-20")
        assert attempt is not None and attempt.outcome == "error"
        assert service.stored_intervals(tomorrow) is None
        # Inside the retry delay: no request, however often it is asked.
        for minute in (0, 1, 14):
            self._set_time(15, minute, 59 if minute == 14 else 0)
            outcome = service.schedule_once()
            assert outcome.status == "waiting"
            assert outcome.target_day == tomorrow
        assert len(transport.urls) == 1

    def test_failure_is_retried_after_15_minutes(self):
        tomorrow = self._tomorrow()
        transport = FakeTransport(urllib.error.URLError("boom"),
                                  elering_payload(tomorrow))
        service = self.make_service(transport)
        self._seed(service, self._today())
        self._set_time(15, 0)
        assert service.schedule_once().status == "error"
        self.clock[0] += timedelta(seconds=RETRY_SECONDS)
        outcome = service.schedule_once()
        assert outcome.status == "ok"
        assert len(transport.urls) == 2
        attempt = service.store.attempt("2026-10-20")
        assert attempt is not None and attempt.outcome == "ok"
        assert service.stored_intervals(tomorrow) is not None

    def test_repeated_failures_keep_the_spacing(self):
        transport = FakeTransport(urllib.error.URLError("a"),
                                  urllib.error.URLError("b"))
        service = self.make_service(transport)
        self._seed(service, self._today())
        self._set_time(15, 0)
        assert service.schedule_once().status == "error"
        self._set_time(15, 15)
        assert service.schedule_once().status == "error"
        self._set_time(15, 29)
        assert service.schedule_once().status == "waiting"
        assert len(transport.urls) == 2

    def test_waiting_for_today_does_not_block_tomorrow(self):
        today, tomorrow = self._today(), self._tomorrow()
        transport = FakeTransport(urllib.error.URLError("boom"),
                                  elering_payload(tomorrow))
        service = self.make_service(transport)
        self._set_time(14, 30)
        assert service.schedule_once().status == "error"   # today fails
        outcome = service.schedule_once()                  # today waits
        assert outcome.status == "ok" and outcome.target_day == tomorrow
        assert service.stored_intervals(today) is None

    def test_manual_refresh_after_failure_is_allowed(self):
        tomorrow = self._tomorrow()
        service = self.make_service(FakeTransport(urllib.error.URLError("boom")))
        self._seed(service, self._today())
        self._set_time(15, 0)
        assert service.schedule_once().status == "error"
        # Manual refresh is independent of the scheduler state.
        manual = FakeTransport(elering_payload(tomorrow))
        service._transport = manual
        result = service.refresh()
        assert result.day == tomorrow
        assert len(manual.urls) == 1
        attempt = service.store.attempt("2026-10-20")
        assert attempt is not None and attempt.outcome == "error"

    def test_interrupted_attempt_waits_then_is_retried(self):
        tomorrow = self._tomorrow()
        service = self.make_service(FakeTransport())  # nothing scripted
        self._seed(service, self._today())
        self._set_time(15, 0)
        # Simulate a crash mid-attempt: the day is marked, then the
        # process dies before the outcome is recorded.
        service.store.mark_attempt("2026-10-20", service.now())
        service.close()
        # "Restart" on the same data dir.
        transport = FakeTransport(elering_payload(tomorrow))
        service2 = self.make_service(transport)
        self._set_time(15, 5)
        outcome = service2.schedule_once()
        assert outcome.status == "waiting"
        assert transport.urls == [], "no refetch inside the retry delay"
        attempt = service2.store.attempt("2026-10-20")
        assert attempt is not None and attempt.outcome == "running"
        self._set_time(15, 15)
        assert service2.schedule_once().status == "ok"
        assert len(transport.urls) == 1

    def test_next_day_is_a_new_attempt(self):
        day1 = self._tomorrow()            # 2026-10-20
        day2 = day1 + timedelta(days=1)    # 2026-10-21
        transport = FakeTransport(elering_payload(day1),
                                  elering_payload(day2))
        service = self.make_service(transport)
        self._seed(service, self._today())
        self._set_time(15, 0)
        assert service.schedule_once().status == "ok"
        # Next local date: day1 is now today and stored; day2 is fetched.
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
