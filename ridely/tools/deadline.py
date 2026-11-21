"""Booking deadline: the last day a resident can call to book a ride."""

from __future__ import annotations

import re
from datetime import date, time, timedelta

from ridely.data import programs


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def federal_holidays(year: int) -> set[date]:
    """U.S. federal holidays (observed dates). Offices like these programs usually close on them."""
    MON, THU = 0, 3
    return {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, MON, 3),  # MLK Day
        _nth_weekday(year, 2, MON, 3),  # Presidents Day
        _last_weekday(year, 5, MON),  # Memorial Day
        _observed(date(year, 6, 19)),  # Juneteenth
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, MON, 1),  # Labor Day
        _nth_weekday(year, 10, MON, 2),  # Columbus Day
        _observed(date(year, 11, 11)),
        _nth_weekday(year, 11, THU, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in federal_holidays(d.year)


def _minus_business_days(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


def _last_business_day_on_or_before(d: date) -> date:
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def _clock(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _fmt(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ").lower().replace("12 pm", "noon")


_HOURS_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*[–-]\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)


def _hours_range(text: str | None) -> tuple[time, time] | None:
    m = _HOURS_RE.search(text or "")
    if not m:
        return None

    def t(h, mi, ap):
        h = int(h) % 12 + (12 if ap.lower() == "pm" else 0)
        return time(h, int(mi or 0))

    return t(m[1], m[2], m[3]), t(m[4], m[5], m[6])


def booking_deadline(program_id: str, trip_date: date, today: date, now: time | None = None,
                     trip_time: time | None = None) -> dict:
    """Last day (and time) to call, from the sheet: notice_business_days, call_by, max_days_ahead, ride_hours.
    Business days skip weekends and U.S. federal holidays."""
    prog = programs().get(program_id)
    if prog is None:
        return {"program_id": program_id, "result": "unknown_program"}
    out: dict = {"program_id": program_id, "name": prog["name"], "trip_date": trip_date.isoformat(),
                 "trip_weekday": trip_date.strftime("%A"), "today": today.isoformat(), "phone": prog["phone"]}
    if prog["book_online"]:
        out["book_online"] = prog["book_online"]
    if prog["ride_hours"]:
        out["ride_hours"] = prog["ride_hours"]
        rng = _hours_range(prog["ride_hours"])
        if trip_time and rng:
            ok = rng[0] <= trip_time <= rng[1]
            out["trip_time_ok"] = ok
            if not ok:
                out["trip_time_note"] = f"The trip time {_fmt(trip_time)} is outside ride hours ({prog['ride_hours']})."

    if trip_date < today:
        out["result"] = "trip_in_past"
        out["message"] = "The trip date is before today. Check the date with the user."
        return out

    n = prog["notice_business_days"]
    if not n:
        out["result"] = "no_booking_needed"
        out["message"] = "No advance booking needed."
        return out
    last = _minus_business_days(trip_date, n)
    cutoff = _clock(prog["call_by"]) if prog["call_by"] else None
    rule = f"at least {n} business day{'s' if n != 1 else ''} before the trip (weekends and holidays do not count)"
    if cutoff:
        rule += f", by {_fmt(cutoff)} on the last day"
        out["call_by_time"] = _fmt(cutoff)
    out["rule"] = rule
    out["last_day_to_call"] = last.isoformat()
    out["last_day_to_call_weekday"] = last.strftime("%A")

    max_adv = prog["max_days_ahead"]
    by = f"{last.strftime('%A, %B %d')}" + (f" at {_fmt(cutoff)}" if cutoff else "")
    if max_adv is not None and (trip_date - today).days > max_adv:
        first = trip_date - timedelta(days=max_adv)
        out["result"] = "too_early"
        out["first_day_to_call"] = first.isoformat()
        out["message"] = f"Too early to book: they take bookings at most {max_adv} days ahead. Call on or after {first.strftime('%A, %B %d')}, and by {by}."
    elif last < today or (last == today and cutoff and now and now > cutoff):
        out["result"] = "too_late"
        out["message"] = (f"Too late for this trip: the last time to call was {by}. "
                          "Say so kindly and suggest the next option.")
    elif last == today:
        out["result"] = "call_today"
        out["message"] = f"Today is the last day to call{f', by {_fmt(cutoff)}' if cutoff else ''}."
    else:
        out["result"] = "ok"
        out["message"] = f"Call by {by}."
    return out
