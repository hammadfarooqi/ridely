"""Booking deadline: the last day a resident can call to book a ride."""

from __future__ import annotations

from datetime import date, timedelta

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


def booking_deadline(program_id: str, trip_date: date, today: date) -> dict:
    prog = programs().get(program_id)
    if prog is None:
        return {"program_id": program_id, "result": "unknown_program"}
    b = prog["booking"]
    notice = b["notice"]
    out: dict = {"program_id": program_id, "name": prog["name"], "trip_date": trip_date.isoformat(),
                 "today": today.isoformat(), "phone": b.get("phone"), "office_hours": b.get("hours")}

    if trip_date < today:
        out["result"] = "trip_in_past"
        out["message"] = "The trip date is before today. Check the date with the user."
        return out

    kind, n = notice["kind"], int(notice["value"])
    if kind == "none":
        out["result"] = "no_booking_needed"
        out["message"] = "No booking needed."
        return out
    if kind == "business_days":
        last = _minus_business_days(trip_date, n)
        rule = f"at least {n} business day{'s' if n != 1 else ''} before the trip"
    else:  # calendar_days
        last = trip_date - timedelta(days=n)
        if notice.get("call_days") != "daily":  # most offices take calls on business days only
            last = _last_business_day_on_or_before(last)
        rule = f"at least {n} day{'s' if n != 1 else ''} before the trip"
    if notice.get("cutoff_time"):
        rule += f", by {notice['cutoff_time']} on the last day"
        out["cutoff_time"] = notice["cutoff_time"]

    out["rule"] = rule
    out["last_day_to_call"] = last.isoformat()
    out["last_day_to_call_weekday"] = last.strftime("%A")
    out["days_until_last_day"] = (last - today).days

    max_adv = b.get("max_advance_days")
    if max_adv is not None and (trip_date - today).days > max_adv:
        first = trip_date - timedelta(days=max_adv)
        out["result"] = "too_early"
        out["first_day_to_call"] = first.isoformat()
        out["message"] = f"Too early to book. Call on or after {first.strftime('%A, %B %d')}."
    elif last < today:
        out["result"] = "too_late"
        out["message"] = f"The booking deadline ({last.strftime('%A, %B %d')}) has passed. Suggest other programs, or tell the user to call and ask anyway."
    elif last == today:
        out["result"] = "call_today"
        out["message"] = "Today is the last day to call. Call during office hours today."
    else:
        out["result"] = "ok"
        out["message"] = f"Call by {last.strftime('%A, %B %d')}."
    if not prog.get("verified", False):
        out["note"] = "Notice rule is from web sources and is not yet confirmed. Tell the user to call early."
    return out
