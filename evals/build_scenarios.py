"""Build evals/generated.yaml: rule-coverage scenarios with expected answers from evals/oracle.py.

    uv run python evals/build_scenarios.py          # write generated.yaml + print the agreement report

How it works
1. A list of rider cases. Fixed "boundary" cases cover every rule edge (age 59/60, 64/65; each disability
   type; wheelchair; Medicaid x medical; each area edge and each also_goes_to stop; ride hours; every
   deadline result: ok, call today, too late, too early, Thanksgiving). Seeded random cases fill in
   ordinary mixes.
2. Each case becomes a user message (English, Spanish, or a caregiver writing for a parent). The message
   gives every fact, so the right answer is fully decided by the rules.
3. The oracle decides the expected programs and deadlines. Those become the checks.
4. Agreement report: the builder also runs Ridely's own code (ridely.tools) on each case and lists every
   case where code and oracle disagree. Each one is a bug in one of them, or a rule to clarify.
"""

from __future__ import annotations

import random
import sys
from datetime import date, time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.oracle import HELP, PHONES, TOWNS, Rider, deadline, judge, load_rules  # noqa: E402

DESTS = {  # id -> (how a user says it, town, is it a medical place)
    "penn_medicine_princeton": ("Penn Medicine Princeton Medical Center", "Plainsboro", True),
    "rwj_hamilton": ("RWJ Hamilton hospital", "Hamilton", True),
    "capital_health_regional": ("Capital Health Regional Medical Center in Trenton", "Trenton", True),
    "st_francis_trenton": ("St. Francis Medical Center in Trenton", "Trenton", True),
    "capital_health_hopewell": ("Capital Health Medical Center in Hopewell", "Hopewell Township", True),
    "rwj_new_brunswick": ("RWJ University Hospital in New Brunswick", "New Brunswick", True),
    "hup_philadelphia": ("the Hospital of the University of Pennsylvania in Philadelphia", "Philadelphia", True),
    "princeton_fitness_wellness": ("Princeton Fitness & Wellness for my prescribed rehab", "Montgomery", True),
    "princeton_shopping_center": ("the Princeton Shopping Center", "Princeton", False),
    "princeton_public_library": ("the Princeton Public Library", "Princeton", False),
    "cmap": ("the Center for Modern Aging in Princeton", "Princeton", False),
    "quaker_bridge_mall": ("Quaker Bridge Mall", "Lawrence", False),
    "whole_foods_princeton": ("Whole Foods on Route 1", "West Windsor", False),
    "lawrence_library": ("the Lawrence library (Mercer County Library)", "Lawrence", False),
    "mercer_social_services": ("the Mercer County Board of Social Services in Trenton", "Trenton", False),
}
HOMES = ["Princeton", "Trenton", "Hamilton", "Lawrence", "Ewing", "West Windsor", "Hopewell Township",
         "Plainsboro", "South Brunswick", "Robbinsville"]
# Weekday trip dates only (weekend service is not in the sheet). Today is Fri Nov 13, 10 am.
DATES = [date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 20), date(2026, 11, 23),
         date(2026, 11, 25), date(2026, 11, 30), date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 10),
         date(2026, 12, 18)]
TIMES = [None, time(9, 0), time(10, 30), time(13, 0), time(14, 30), time(16, 30)]

ES_MONTH = {11: "noviembre", 12: "diciembre"}
ES_DAY = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def date_words(d: date, es: bool = False) -> str:
    if es:
        return f"el {ES_DAY[d.weekday()]} {d.day} de {ES_MONTH[d.month]}"
    return d.strftime("%A, %B ") + str(d.day)


def date_forms(d: date) -> list[str]:
    """Ways a reply may write a date (for the deadline check)."""
    forms = [d.strftime("%B ") + str(d.day), d.strftime("%b ") + str(d.day), d.strftime("%b. ") + str(d.day),
             f"{d.month}/{d.day}", f"{d.day} de {ES_MONTH.get(d.month, '')}"]
    if d == date(2026, 11, 13):
        forms += ["today", "hoy"]
    elif (d - date(2026, 11, 13)).days <= 6:
        forms += [d.strftime("%A"), ES_DAY[d.weekday()]]
    return forms


def time_words(t: time | None, es: bool) -> str:
    if not t:
        return ""
    s = t.strftime("%I:%M %p").lstrip("0").replace(":00", "").lower()
    return f" a las {t.strftime('%H:%M')}" if es else f" at {s}"


def message(r: Rider, style: str) -> str:
    es = style == "es"
    dest, _, _ = DESTS[r.dest_id]
    if es:
        parts = [f"Tengo {r.age} años y vivo en {r.home}."]
        parts.append("Uso silla de ruedas." if r.wheelchair else "No uso silla de ruedas.")
        if r.physical:
            parts.append("Tengo una discapacidad física, me cuesta caminar.")
        if r.visual:
            parts.append("Soy ciega.")
        if r.other_dis:
            parts.append("Tengo una discapacidad que me hace difícil viajar.")
        if not (r.physical or r.visual or r.other_dis or r.wheelchair):
            parts.append("No tengo ninguna discapacidad.")
        parts.append("Estoy certificada para Access Link." if r.certified else "No estoy certificada para Access Link.")
        parts.append("Tengo Medicaid (NJ FamilyCare)." if r.medicaid else "No tengo Medicaid.")
        if r.crosstown_registered is not None:
            parts.append("Ya estoy inscrita en Crosstown." if r.crosstown_registered else "No estoy inscrita en ningún programa.")
        why = "para una cita médica" if r.medical else "para hacer compras y diligencias"
        parts.append(f"Necesito ir a {dest} {date_words(r.trip_date, True)}{time_words(r.trip_time, True)} {why}. ¿Cómo puedo ir?")
        return " ".join(parts)

    care = style == "caregiver"
    I, my, am = ("She", "Her", "is") if care else ("I", "My", "am")
    have = "has" if care else "have"
    use = "uses" if care else "use"
    parts = ["I'm texting for my mother." if care else "Hi.",
             f"{'She is' if care else 'I am'} {r.age} and {'lives' if care else 'live'} in {r.home}."]
    parts.append(f"{I} {use} a wheelchair." if r.wheelchair else f"{I} {'does' if care else 'do'} not use a wheelchair.")
    if r.physical:
        parts.append(f"{I} {have} a physical disability and {'has' if care else 'have'} trouble walking.")
    if r.visual:
        parts.append(f"{I} {am} legally blind.")
    if r.other_dis:
        parts.append(f"{I} {have} a disability that makes travel hard.")
    if not (r.physical or r.visual or r.other_dis or r.wheelchair):
        parts.append(f"{I} {have} no disability.")
    parts.append(f"{I} {am} certified for Access Link." if r.certified else f"{I} {am} not certified for Access Link.")
    parts.append(f"{I} {have} NJ Medicaid." if r.medicaid else f"{I} {'does' if care else 'do'} not have Medicaid.")
    if r.crosstown_registered is not None:
        parts.append(f"{I} {am} already registered with Crosstown." if r.crosstown_registered
                     else f"{I} {am} not signed up with any ride program yet.")
    why = "for a doctor's appointment" if r.medical else "for errands, not a medical visit"
    who = "She needs" if care else "I need"
    parts.append(f"{who} a ride to {dest} on {date_words(r.trip_date)}{time_words(r.trip_time, False)} {why}.")
    return " ".join(parts)


def _phones(pids) -> set[str]:
    return {p for pid in pids for p in PHONES[pid]}


def expectations(r: Rider) -> tuple[dict, str]:
    rules = {p["id"]: p for p in load_rules()}
    v = judge(r)
    offer = [p for p, s in v.status.items() if s == "fits" and v.deadline[p]["result"] in ("ok", "call_today")]
    late = [p for p, s in v.status.items() if s == "fits" and v.deadline[p]["result"] == "too_late"]
    early = [p for p, s in v.status.items() if s == "fits" and v.deadline[p]["result"] == "too_early"]
    future = [p for p, s in v.status.items() if s == "future_only"]
    no = [p for p, s in v.status.items() if s == "no"]
    allowed = _phones([p for p in v.status if v.status[p] != "no"])
    exp: dict = {}
    if offer:
        exp["expect_phones_any"] = sorted(_phones(offer))
        days = {v.deadline[p]["last_day"] for p in offer}
        exp["expect_deadline_any"] = sorted({f for d in days for f in date_forms(d)})
        outcome = "offer " + ", ".join(offer)
    elif early:
        exp["expect_phones_any"] = sorted(_phones(early))
        firsts = {r.trip_date - __import__("datetime").timedelta(days=rules[p]["max_ahead"]) for p in early}
        exp["expect_text_any"] = sorted({f for d in firsts for f in date_forms(d)} |
                                        {"too early", "on or after", "not yet", "demasiado pronto", "a partir"})
        outcome = "too early for " + ", ".join(early)
    elif late:
        exp["expect_text_any"] = ["too late", "passed", "not enough", "demasiado tarde", "ya pasó", "no hay tiempo",
                                  "deadline was", "needed", *HELP]
        outcome = "too late for " + ", ".join(late)
    else:
        exp["expect_text_any"] = HELP
        outcome = "nothing fits" + (f" (future: {', '.join(future)})" if future else "")
    forbid = sorted(_phones(no) - allowed)
    if forbid:
        exp["forbid_phones"] = forbid
    why = "; ".join(f"{p}: {s}" + (f" ({v.why[p]})" if p in v.why else "") +
                    (f" [{v.deadline[p]['result']} {v.deadline[p]['last_day']}]" if p in v.deadline else "")
                    for p, s in v.status.items())
    return exp, f"{outcome}. {why}"


# ---------- cases ----------

def R(home, age, dest, d, **kw):
    t = kw.pop("t", None)
    return Rider(home=home, age=age, dest_id=dest, dest_town=DESTS[dest][1], trip_date=d, trip_time=t,
                 medical=kw.pop("medical", DESTS[dest][2]), **kw)


D = DATES
BOUNDARY: list[tuple[str, Rider]] = [
    # age edges
    ("age_59_trade", R("Trenton", 59, "st_francis_trenton", D[8])),
    ("age_60_trade", R("Trenton", 60, "st_francis_trenton", D[8])),
    ("age_64_crosstown", R("Princeton", 64, "princeton_shopping_center", D[8], crosstown_registered=True)),
    ("age_65_crosstown", R("Princeton", 65, "princeton_shopping_center", D[8], crosstown_registered=True)),
    ("age_95_princeton", R("Princeton", 95, "princeton_public_library", D[3])),
    # disability types
    ("physical_50_crosstown", R("Princeton", 50, "cmap", D[8], physical=True, crosstown_registered=True)),
    ("visual_50_crosstown_no", R("Princeton", 50, "cmap", D[8], visual=True)),
    ("visual_40_trade", R("Hamilton", 40, "quaker_bridge_mall", D[8], visual=True)),
    ("other_dis_45_trade", R("Ewing", 45, "capital_health_regional", D[7], other_dis=True)),
    ("certified_30_access", R("Trenton", 30, "quaker_bridge_mall", D[3], certified=True, other_dis=True)),
    ("certified_not_trade_area", R("Plainsboro", 50, "princeton_public_library", D[3], certified=True, physical=True)),
    ("disabled_not_certified_plainsboro", R("Plainsboro", 40, "princeton_public_library", D[8], physical=True)),
    # wheelchair
    ("wheelchair_70_princeton", R("Princeton", 70, "princeton_shopping_center", D[8], wheelchair=True)),
    ("wheelchair_medicaid_doctor", R("Trenton", 40, "capital_health_regional", D[7], wheelchair=True, medicaid=True)),
    # Medicaid x medical
    ("medicaid_medical_45", R("Trenton", 45, "st_francis_trenton", D[7], medicaid=True)),
    ("medicaid_not_medical_45", R("Trenton", 45, "mercer_social_services", D[7], medicaid=True)),
    ("medicaid_new_brunswick_45", R("Hamilton", 45, "rwj_new_brunswick", D[8], medicaid=True)),
    ("medicaid_philadelphia", R("Princeton", 50, "hup_philadelphia", D[8], medicaid=True)),
    ("no_medicaid_philadelphia_70", R("Princeton", 70, "hup_philadelphia", D[8])),
    # areas and extra stops
    ("princeton_to_pmc_70", R("Princeton", 70, "penn_medicine_princeton", D[4])),
    ("hamilton_to_pmc_70", R("Hamilton", 70, "penn_medicine_princeton", D[4])),
    ("princeton_to_rehab_70", R("Princeton", 70, "princeton_fitness_wellness", D[4], crosstown_registered=True)),
    ("hamilton_to_rehab_70", R("Hamilton", 70, "princeton_fitness_wellness", D[4])),
    ("princeton_to_hamilton_70", R("Princeton", 70, "rwj_hamilton", D[3])),
    ("trenton_to_new_brunswick_70", R("Trenton", 70, "rwj_new_brunswick", D[8])),
    ("plainsboro_66_nothing_now", R("Plainsboro", 66, "princeton_public_library", D[8])),
    ("south_brunswick_70", R("South Brunswick", 70, "princeton_shopping_center", D[8])),
    ("robbinsville_70", R("Robbinsville", 70, "rwj_hamilton", D[8])),
    ("young_no_dis_princeton", R("Princeton", 40, "princeton_public_library", D[8])),
    # ride hours
    ("trade_after_hours", R("Trenton", 70, "st_francis_trenton", D[8], t=time(16, 30))),
    ("trade_before_hours", R("Trenton", 70, "st_francis_trenton", D[8], t=time(7, 0))),
    ("crosstown_after_hours", R("Princeton", 70, "princeton_public_library", D[8], t=time(17, 30), crosstown_registered=True)),
    # deadlines
    ("mon_crosstown_noon_today", R("Princeton", 75, "princeton_shopping_center", D[0], crosstown_registered=True)),
    ("mon_trade_too_late", R("Trenton", 75, "st_francis_trenton", D[0])),
    ("tue_trade_call_today", R("Trenton", 75, "st_francis_trenton", D[1])),
    ("tue_medicaid_call_today", R("Trenton", 45, "st_francis_trenton", D[1], medicaid=True)),
    ("mon_medicaid_too_late", R("Trenton", 45, "st_francis_trenton", D[0], medicaid=True)),
    ("access_mon_call_today", R("Hamilton", 40, "quaker_bridge_mall", D[0], certified=True, physical=True)),
    ("thanksgiving_medicaid", R("Trenton", 45, "capital_health_regional", D[6], medicaid=True)),
    ("thanksgiving_trade", R("Hamilton", 70, "rwj_hamilton", D[6])),
    ("trade_too_early", R("Trenton", 70, "st_francis_trenton", D[10])),
    ("access_too_early", R("Trenton", 30, "quaker_bridge_mall", D[9], certified=True, physical=True)),
    ("medicaid_too_early", R("Trenton", 45, "st_francis_trenton", D[10], medicaid=True)),
    ("trade_14_days_ok", R("Ewing", 70, "capital_health_regional", D[7])),
]


QUOTA = {"offer": 0.62, "nothing": 0.16, "too early": 0.11, "too late": 0.11}  # share of the random cases


def random_cases(n: int, seed: int = 420) -> list[tuple[str, Rider]]:
    """Seeded random riders, kept until each outcome type has its share (QUOTA). Ordinary mixes, many towns."""
    rng = random.Random(seed)
    want = {k: round(v * n) for k, v in QUOTA.items()}
    want["offer"] += n - sum(want.values())
    out = []
    while len(out) < n:
        dest = rng.choice(list(DESTS))
        dis = rng.choices(["none", "physical", "visual", "other", "wheelchair"], [6, 2, 1, 1, 1])[0]
        r = R(rng.choice(HOMES), rng.choice([35, 45, 58, 61, 63, 66, 68, 72, 77, 81, 88]), dest, rng.choice(DATES),
              t=rng.choice(TIMES), physical=dis == "physical", visual=dis == "visual", other_dis=dis == "other",
              wheelchair=dis == "wheelchair", medicaid=rng.random() < 0.25,
              certified=dis != "none" and rng.random() < 0.3,
              crosstown_registered=rng.choice([None, True, False]))
        _, note = expectations(r)
        kind = next(k for k in QUOTA if note.startswith(k if k != "nothing" else "nothing fits"))
        if want[kind] > 0:
            want[kind] -= 1
            out.append((f"mix_{len(out) + 1:03d}", r))
    return out


def code_status(r: Rider) -> dict[str, str]:
    """What Ridely's own code says, for the agreement report."""
    from ridely.geo import Place
    from ridely.tools.deadline import booking_deadline
    from ridely.tools.eligibility import Person, check_eligibility
    from ridely.tools.service_area import check_service_area

    def place(town):
        c, s = TOWNS[town]
        return Place(query=town, town=town, county=c, state=s)

    person = Person(age=r.age, physical_disability=r.physical, visually_impaired=r.visual,
                    other_disability=r.other_dis, access_link_certified=r.certified, uses_wheelchair=r.wheelchair,
                    has_medicaid=r.medicaid, trip_is_medical=r.medical, home=place(r.home))
    out = {}
    for e in check_eligibility(person):
        pid, st = e["program_id"], e["status"]
        if st in ("likely", "maybe", "future_only"):
            area = check_service_area(pid, place(r.home), place(r.dest_town), end_destination_id=r.dest_id)
            dl = booking_deadline(pid, r.trip_date, date(2026, 11, 13), now=time(10, 0), trip_time=r.trip_time)
            if area["result"] == "out" or dl.get("trip_time_ok") is False:
                st = "no"
            elif st != "future_only":
                st = "fits"
        out[pid] = st
    return out


HEADER = ("# GENERATED by evals/build_scenarios.py from data/programs.csv + evals/oracle.py. Do not edit by hand:\n"
          "# change the builder or the oracle and run it again. Today = Fri 2026-11-13 10:00.\n")


def build() -> tuple[list[dict], list[tuple[str, dict]]]:
    """Returns (scenarios, disagreements between oracle and Ridely's code)."""
    styles = ["en"] * 7 + ["caregiver"] * 2 + ["es"]
    cases = BOUNDARY + random_cases(150 - 45 - len(BOUNDARY))
    rng = random.Random(7)
    scenarios, disagree = [], []
    for i, (cid, r) in enumerate(cases):
        style = "es" if cid.endswith("_es") else rng.choice(styles)
        exp, note = expectations(r)
        sc = {"id": f"gen_{cid}", "category": "rules", "style": style, "note": note,
              "turns": [message(r, style)], **exp}
        if i % 5 == 4:
            sc["holdout"] = True
        scenarios.append(sc)
        mine = {p: s for p, s in judge(r).status.items()}
        code = code_status(r)
        diff = {p: (mine[p], code[p]) for p in mine if mine[p] != code[p]}
        if diff:
            disagree.append((cid, diff))
    return scenarios, disagree


def render(scenarios: list[dict]) -> str:
    return HEADER + yaml.safe_dump({"scenarios": scenarios}, sort_keys=False, allow_unicode=True, width=120)


def main() -> None:
    scenarios, disagree = build()
    out = ROOT / "evals/generated.yaml"
    out.write_text(render(scenarios))
    print(f"wrote {len(scenarios)} scenarios to {out.relative_to(ROOT)} "
          f"({sum(1 for s in scenarios if s.get('holdout'))} holdout)")
    print(f"code vs oracle disagreements: {len(disagree)}")
    for cid, diff in disagree:
        print(f"  {cid}: " + ", ".join(f"{p} oracle={a} code={b}" for p, (a, b) in diff.items()))


if __name__ == "__main__":
    main()
