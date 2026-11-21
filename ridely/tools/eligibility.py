"""Deterministic eligibility rules from the rules sheet. The model asks the questions; this code decides.

A program fits only if every check passes (same rules as the team's sheet):
  - age >= min_age, OR the rider has the disability in or_disability (both blank = anyone)
  - needs_medicaid = yes -> the rider has NJ Medicaid / NJ FamilyCare
  - the rider's home is inside the area
  - medical_only = yes -> the trip is medical
  - wheelchair user -> wheelchair is "yes" or "with doctor's form"

Status meanings:
  likely      -> every check passes
  maybe       -> no check fails, but some facts are missing
  future_only -> the rider fits, but cannot use it for this trip (waitlist, or must get certified first)
  no          -> a check fails
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ridely.data import programs
from ridely.geo import Place


@dataclass
class Person:
    age: int | None = None
    physical_disability: bool | None = None
    visually_impaired: bool | None = None
    other_disability: bool | None = None  # any other disability that makes travel hard
    access_link_certified: bool | None = None
    uses_wheelchair: bool | None = None
    has_medicaid: bool | None = None  # NJ Medicaid / NJ FamilyCare member
    trip_is_medical: bool | None = None
    home: Place | None = None

    def any_disability(self) -> bool | None:
        vals = [self.physical_disability, self.visually_impaired, self.other_disability,
                self.access_link_certified, self.uses_wheelchair]
        if any(v is True for v in vals):
            return True
        if all(v is False for v in vals[:3]):
            return False
        return None


@dataclass
class Result:
    program_id: str
    name: str
    status: str = "maybe"
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    sign_up: str = ""

    def to_dict(self) -> dict:
        return {"program_id": self.program_id, "name": self.name, "status": self.status,
                "reasons": self.reasons, "missing_info": self.missing, "sign_up": self.sign_up}


_DIS_LABEL = {"any": "has a disability", "physical": "has a physical disability",
              "visual": "is blind or has low vision", "certified": "is certified for Access Link"}
_DIS_MUST = {"any": "have a disability", "physical": "have a physical disability",
             "visual": "be blind or have low vision", "certified": "be certified for Access Link"}
_DIS_MISSING = {"any": "disability (yes/no)", "physical": "physical disability (yes/no)",
                "visual": "vision problem (yes/no)", "certified": "Access Link certification (yes/no)"}


def _condition(kind: str, p: Person) -> bool | None:
    if kind == "any":
        return p.any_disability()
    if kind == "physical":
        return True if p.uses_wheelchair else p.physical_disability
    if kind == "visual":
        return p.visually_impaired
    if kind == "certified":
        return p.access_link_certified
    return None


def _age_or_condition(prog: dict, p: Person, r: Result) -> bool | None:
    min_age, kind = prog["min_age"], prog["or_disability"]
    if min_age is None and kind is None:
        return True
    age_ok = None if (min_age is None or p.age is None) else p.age >= min_age
    cond_ok = None if kind is None else _condition(kind, p)
    if cond_ok:
        r.reasons.append(f"The rider {_DIS_LABEL[kind]}, so age does not matter.")
        return True
    if age_ok:
        r.reasons.append(f"Age {p.age} meets the {min_age}+ rule.")
        return True
    if min_age is None:
        age_ok = False
    if kind is None:
        cond_ok = False
    if age_ok is False and cond_ok is False:
        need = [f"be {min_age} or older"] if min_age else []
        need += [_DIS_MUST[kind]] if kind else []
        r.reasons.append(f"Rider must {' or '.join(need)}.")
        return False
    if age_ok is None and min_age is not None:
        r.missing.append("age")
    if cond_ok is None and kind is not None:
        r.missing.append(_DIS_MISSING[kind])
    return None


def _home_in_area(prog: dict, home: Place | None, r: Result) -> bool | None:
    area = prog["area"]
    if area["near_bus_routes"]:
        r.reasons.append("Only near local bus routes (about 3/4 mile). Ridely cannot check this; Access Link can.")
        return True
    if home is None or (home.town is None and home.county is None and home.state is None):
        r.missing.append("home town")
        return None
    ok = (home.town in area["towns"]) or (home.county in area["counties"]) or (home.state in area["states"])
    if ok:
        r.reasons.append(f"Home ({home.town}, {home.county} County) is in the area: {area['text']}.")
        r.reasons += area["notes"]
    else:
        r.reasons.append(f"Home must be in: {area['text']}. Home is {home.town}, {home.county} County.")
    return ok


def _flag(value: bool | None, needed: bool, label_missing: str, reason_fail: str, reason_ok: str, r: Result):
    if not needed:
        return True
    if value is None:
        r.missing.append(label_missing)
        return None
    r.reasons.append(reason_ok if value else reason_fail)
    return value


def _wheelchair(prog: dict, p: Person, r: Result) -> bool | None:
    wc = prog["wheelchair"]
    if wc == "no":
        if p.uses_wheelchair is None:
            r.reasons.append("No wheelchairs: the rider must be able to get in and out of the car on their own.")
            return True
        if p.uses_wheelchair:
            r.reasons.append("No wheelchairs: the rider must get in and out of the car on their own.")
            return False
        return True
    if p.uses_wheelchair and wc == "with doctor's form":
        r.reasons.append("Wheelchair van only after the doctor faxes a medical necessity form.")
    elif p.uses_wheelchair:
        r.reasons.append("Has wheelchair-accessible vehicles.")
    return True


def _combine(*checks: bool | None) -> str:
    if any(c is False for c in checks):
        return "no"
    if any(c is None for c in checks):
        return "maybe"
    return "likely"


def _evaluate(pid: str, prog: dict, p: Person) -> Result:
    r = Result(program_id=pid, name=prog["name"], sign_up=prog["sign_up_text"])
    age_cond = _age_or_condition(prog, p, r)
    checks = [
        age_cond,
        _flag(p.has_medicaid, prog["needs_medicaid"], "NJ Medicaid / NJ FamilyCare (yes/no)",
              "Only for NJ Medicaid / NJ FamilyCare members.", "NJ Medicaid / NJ FamilyCare member.", r),
        _home_in_area(prog, p.home, r),
        _flag(p.trip_is_medical, prog["medical_only"], "is the trip medical (yes/no)",
              "Only for medical trips.", "The trip is medical.", r),
        _wheelchair(prog, p, r),
    ]
    r.status = _combine(*checks)
    if r.status == "no":
        r.missing = []  # a failed check decides it; more facts do not help

    kind = prog["sign_up"]
    if kind == "waitlist" and r.status != "no":
        r.status = "future_only"
        r.reasons.append("Not for this trip: new riders join a waitlist that takes months. "
                         "Mention it only as an option for future trips.")
    elif kind == "certification first" and age_cond is False and p.any_disability():
        r.status = "future_only"
        r.reasons.append("Not certified yet, so not for this trip. Can apply now for future trips.")
    elif kind == "certification first" and r.status == "maybe" and "Access Link certification (yes/no)" in r.missing:
        r.reasons.append("Ask if the rider is already certified for Access Link.")
    elif kind == "register first" and r.status != "no":
        r.reasons.append("Must be registered before booking. Ask if the rider already is. If not, explain how to sign up.")
    elif kind == "on first call" and r.status != "no":
        r.reasons.append("New riders register on their first call.")
    return r


ORDER = {"likely": 0, "maybe": 1, "future_only": 2, "no": 3}


def check_eligibility(person: Person) -> list[dict]:
    """Results in the sheet's row order, grouped by status (likely first)."""
    results = [_evaluate(pid, prog, person) for pid, prog in programs().items()]
    results.sort(key=lambda r: ORDER[r.status])  # sort is stable: keeps row order inside each status
    return [r.to_dict() for r in results]
