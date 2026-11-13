"""Deterministic eligibility rules. The model asks the questions; this code decides.

Status meanings:
  likely -> every rule we know passes
  maybe  -> no rule fails, but some facts are missing or the program needs approval
  no     -> a rule fails
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ridely.data import programs
from ridely.geo import Place


@dataclass
class Person:
    age: int | None = None
    visually_impaired: bool | None = None
    has_disability: bool | None = None  # any disability that makes travel hard
    has_medicaid: bool | None = None  # NJ Medicaid / NJ FamilyCare member
    trip_is_medical: bool | None = None
    home: Place | None = None


@dataclass
class Result:
    program_id: str
    name: str
    status: str
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "program_id": self.program_id,
            "name": self.name,
            "status": self.status,
            "reasons": self.reasons,
            "missing_info": self.missing,
        }


def _residency(rules: dict, home: Place | None, r: Result) -> bool | None:
    towns = rules.get("home_towns") or []
    counties = rules.get("home_counties") or []
    if not towns and not counties:
        return True
    if home is None or (home.town is None and home.county is None):
        r.missing.append("home town")
        return None
    ok = (home.town in towns) or (home.county in counties)
    where = ", ".join([*towns, *[f"{c} County" for c in counties]])
    if ok:
        r.reasons.append(f"Lives in the area ({home.town}, {home.county} County).")
    else:
        r.reasons.append(f"Must live in: {where}. Home is {home.town}, {home.county} County.")
    return ok


def _age_or_condition(rules: dict, p: Person, r: Result) -> bool | None:
    """Age rule, with 'any age' paths for visual impairment or disability."""
    min_age = rules.get("min_age")
    alt_vi = rules.get("visually_impaired_any_age", False)
    alt_dis = rules.get("disability_any_age", False)
    if alt_vi and p.visually_impaired:
        r.reasons.append("Visually impaired adults of any age can use it.")
        return True
    if alt_dis and (p.has_disability or p.visually_impaired):
        r.reasons.append("People with a disability can use it at any age.")
        return True
    if min_age is None:
        return True
    if p.age is None:
        r.missing.append("age")
        return None
    if p.age >= min_age:
        r.reasons.append(f"Age {p.age} meets the {min_age}+ rule.")
        return True
    alt = []
    if alt_vi:
        alt.append("visually impaired")
        if p.visually_impaired is None:
            r.missing.append("visual impairment (yes/no)")
    if alt_dis:
        alt.append("has a disability")
        if p.has_disability is None:
            r.missing.append("disability (yes/no)")
    if alt and r.missing:
        r.reasons.append(f"Age {p.age} is under {min_age}. Still possible if the person is {' or '.join(alt)}.")
        return None
    r.reasons.append(f"Must be {min_age} or older{' or ' + ' or '.join(alt) if alt else ''}. Age is {p.age}.")
    return False


def _combine(*checks: bool | None) -> str:
    if any(c is False for c in checks):
        return "no"
    if any(c is None for c in checks):
        return "maybe"
    return "likely"


def _evaluate(pid: str, prog: dict, p: Person) -> Result:
    rules = prog["eligibility"]
    r = Result(program_id=pid, name=prog["name"], status="maybe")
    rule = rules["rule"]

    if rule in ("crosstown", "rideprovide", "trade"):
        r.status = _combine(_residency(rules, p.home, r), _age_or_condition(rules, p, r))
        if r.status != "no" and rules.get("needs_registration"):
            r.reasons.append("Must register or join before the first ride.")

    elif rule == "access_link":
        if p.has_disability or p.visually_impaired:
            r.status = "maybe"
            r.reasons.append(
                "Needs NJ Transit approval (phone interview) first. Only near local bus routes."
            )
        elif p.has_disability is None:
            r.status = "maybe"
            r.missing.append("disability (yes/no)")
            r.reasons.append("Only for people whose disability stops them from using the bus.")
        else:
            r.status = "no"
            r.reasons.append("Only for people whose disability stops them from using the bus.")

    elif rule == "medicaid_nemt":
        med = p.has_medicaid
        trip = p.trip_is_medical
        if med is False:
            r.status = "no"
            r.reasons.append("Only for NJ Medicaid / NJ FamilyCare members.")
        elif trip is False:
            r.status = "no"
            r.reasons.append("Only for medical visits.")
        else:
            if med is None:
                r.missing.append("Medicaid / NJ FamilyCare (yes/no)")
            if trip is None:
                r.missing.append("is the trip a medical visit (yes/no)")
            r.status = "likely" if (med and trip) else "maybe"
            if med:
                r.reasons.append("NJ Medicaid / NJ FamilyCare member.")
            if trip:
                r.reasons.append("Trip is a medical visit.")

    elif rule == "everyone":
        r.status = "likely"
        r.reasons.append("Open to everyone. Check the route and schedule.")

    else:  # unknown rule: never claim eligibility
        r.status = "maybe"
        r.reasons.append("No rule in code for this program.")

    return r


def check_eligibility(person: Person) -> list[dict]:
    order = {"likely": 0, "maybe": 1, "no": 2}
    results = [_evaluate(pid, prog, person) for pid, prog in programs().items()]
    results.sort(key=lambda r: order[r.status])
    return [r.to_dict() for r in results]
