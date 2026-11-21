"""Independent oracle: what the right answer is for a rider, from the rules sheet and Cyron's written rules.

This file does NOT import ridely.tools. It is a second, separate implementation of the rules in
data/programs.csv + the "STEPS" in Cyron's prompt, written from the text of those rules. The scenario
builder uses it to write the expected answers. If the agent's code tools and this oracle disagree, one of
them has a bug (or the rules are unclear), and the scenario fails until a person decides which is right.

One deliberate difference from Cyron's text: business days also skip U.S. federal holidays (the programs'
offices are closed), not only weekends.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Fixed "now" for every scenario: Friday, November 13, 2026, 10:00 am, New Jersey.
NOW = datetime(2026, 11, 13, 10, 0)

HOLIDAYS = {date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25), date(2027, 1, 1), date(2027, 1, 18)}

# Where places are. Homes and destinations used by the builder.
TOWNS = {  # town -> (county, state)
    "Princeton": ("Mercer", "NJ"), "Trenton": ("Mercer", "NJ"), "Hamilton": ("Mercer", "NJ"),
    "Lawrence": ("Mercer", "NJ"), "Ewing": ("Mercer", "NJ"), "West Windsor": ("Mercer", "NJ"),
    "Hopewell Township": ("Mercer", "NJ"), "East Windsor": ("Mercer", "NJ"), "Robbinsville": ("Mercer", "NJ"),
    "Plainsboro": ("Middlesex", "NJ"), "New Brunswick": ("Middlesex", "NJ"), "South Brunswick": ("Middlesex", "NJ"),
    "Montgomery": ("Somerset", "NJ"), "Philadelphia": ("Philadelphia", "PA"),
}

# Program -> phone numbers (last 10 digits) that count as "offering" it.
PHONES = {
    "medicaid_nemt": ["866-527-9933"],
    "trade": ["609-530-1971", "609-275-2929"],
    "crosstown": ["609-452-5144"],
    "access_link": ["973-491-4224", "800-955-2321"],
    "rideprovide": ["609-452-5144"],  # members' line; joining is 609-452-5140
}
HELP = ["609-751-9699", "2-1-1", "211"]

_IDS = {"Medicaid rides (ModivCare)": "medicaid_nemt", "TRADE (Mercer County)": "trade",
        "Crosstown (CMAP)": "crosstown", "NJ Transit Access Link": "access_link", "RideProvide (GMTMA)": "rideprovide"}
_ALSO = {"Penn Medicine Princeton Health": "penn_medicine_princeton",
         "Princeton Fitness & Wellness": "princeton_fitness_wellness"}


def load_rules() -> list[dict]:
    with (ROOT / "data/programs.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        area = [a.strip() for a in r["area"].split(";")]
        out.append({
            "id": _IDS[r["program"]],
            "min_age": int(r["min_age"]) if r["min_age"] else None,
            "dis": r["or_disability"] or None,
            "medicaid": r["needs_medicaid"] == "yes",
            "area": area,
            "also": [_ALSO[a.strip()] for a in r["also_goes_to"].split(";") if a.strip()],
            "medical_only": r["medical_only"] == "yes",
            "wheelchair": r["wheelchair"],
            "sign_up": r["sign_up"].split(" (")[0],
            "notice": int(r["notice_business_days"]),
            "call_by": time.fromisoformat(r["call_by"]) if r["call_by"] else None,
            "max_ahead": int(r["max_days_ahead"]) if r["max_days_ahead"] else None,
            "hours": r["ride_hours"],
        })
    return out


@dataclass
class Rider:
    home: str
    age: int
    dest_id: str
    dest_town: str
    trip_date: date
    trip_time: time | None = None
    medical: bool = False
    physical: bool = False
    visual: bool = False
    other_dis: bool = False
    certified: bool = False
    wheelchair: bool = False
    medicaid: bool = False
    crosstown_registered: bool | None = None


@dataclass
class Verdict:
    status: dict[str, str] = field(default_factory=dict)  # program -> fits | future_only | no
    why: dict[str, str] = field(default_factory=dict)
    deadline: dict[str, dict] = field(default_factory=dict)  # program -> {result, last_day, call_by}


def _in_area(area: list[str], town: str) -> bool | None:
    county, state = TOWNS[town]
    for a in area:
        a_l = a.lower()
        if a_l.startswith("within"):
            return None  # near bus routes: cannot know
        if a_l == "new jersey" and state == "NJ":
            return True
        if a_l == "mercer county" and county == "Mercer":
            return True
        if a_l == town.lower():
            return True
        if a_l.startswith("montgomery") and town == "Montgomery":
            return True
    return False


def _hours_ok(hours: str, t: time | None) -> bool:
    if not t or not hours or "am" not in hours.lower():
        return True
    import re
    m = re.search(r"(\d{1,2})(?::(\d{2}))?(am|pm)–(\d{1,2})(?::(\d{2}))?(am|pm)", hours.replace(" ", ""))
    if not m:
        return True

    def tt(h, mi, ap):
        return time(int(h) % 12 + (12 if ap == "pm" else 0), int(mi or 0))

    return tt(m[1], m[2], m[3]) <= t <= tt(m[4], m[5], m[6])


def _business(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def deadline(p: dict, trip: date, now: datetime = NOW) -> dict:
    d, n = trip, p["notice"]
    while n:
        d -= timedelta(days=1)
        if _business(d):
            n -= 1
    today = now.date()
    if p["max_ahead"] is not None and (trip - today).days > p["max_ahead"]:
        res = "too_early"
    elif d < today or (d == today and p["call_by"] and now.time() > p["call_by"]):
        res = "too_late"
    elif d == today:
        res = "call_today"
    else:
        res = "ok"
    return {"result": res, "last_day": d, "call_by": p["call_by"]}


def judge(r: Rider, rules: list[dict] | None = None) -> Verdict:
    v = Verdict()
    for p in rules or load_rules():
        pid, why = p["id"], []
        # age OR disability
        cond = {"any": r.physical or r.visual or r.other_dis or r.wheelchair or r.certified,
                "physical": r.physical or r.wheelchair, "visual": r.visual, "certified": r.certified,
                None: False}[p["dis"]]
        age_ok = p["min_age"] is not None and r.age >= p["min_age"]
        if (p["min_age"] is not None or p["dis"] is not None) and not (age_ok or cond):
            why.append("age/disability")
        if p["medicaid"] and not r.medicaid:
            why.append("medicaid")
        home_in = _in_area(p["area"], r.home)
        dest_in = True if r.dest_id in p["also"] else _in_area(p["area"], r.dest_town)
        if home_in is False or dest_in is False:
            why.append("area")
        if p["medical_only"] and not r.medical:
            why.append("not medical")
        if r.wheelchair and p["wheelchair"] == "no":
            why.append("wheelchair")
        if not _hours_ok(p["hours"], r.trip_time):
            why.append("ride hours")
        if why:
            v.status[pid], v.why[pid] = "no", ", ".join(why)
            continue
        if p["sign_up"] == "waitlist":
            v.status[pid], v.why[pid] = "future_only", "waitlist"
            continue
        v.status[pid] = "fits"
        v.deadline[pid] = deadline(p, r.trip_date)
    # certification first: a rider with a disability who is not certified can apply for later trips
    if v.status.get("access_link") == "no" and v.why.get("access_link") == "age/disability" and (
            r.physical or r.visual or r.other_dis or r.wheelchair):
        v.status["access_link"], v.why["access_link"] = "future_only", "not certified yet"
    return v
