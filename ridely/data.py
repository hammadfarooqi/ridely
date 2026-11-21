"""Load program facts from data/programs.csv (the team's rules sheet) and destinations from data/destinations.yaml.

The CSV is the one source of truth. The team edits the "Rules" tab of the Google Sheet, exports it as CSV,
and saves it as data/programs.csv. This module turns each row into a structured program for the tools.
If a cell has a value that the code does not understand (for example a new area name), loading fails with
a clear error, so `uv run pytest` catches it before deploy.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Sheet "program" name -> id used in code and by the model.
PROGRAM_IDS = {
    "Medicaid rides (ModivCare)": "medicaid_nemt",
    "TRADE (Mercer County)": "trade",
    "Crosstown (CMAP)": "crosstown",
    "NJ Transit Access Link": "access_link",
    "RideProvide (GMTMA)": "rideprovide",
}

# Each part of the "area" cell (split on ";") -> what it means. Add a line here when the sheet gets a new area.
AREA_PARTS: dict[str, dict[str, Any]] = {
    "new jersey": {"states": ["NJ"]},
    "mercer county": {"counties": ["Mercer"]},
    "princeton": {"towns": ["Princeton"]},
    "plainsboro": {"towns": ["Plainsboro"]},
    "montgomery south of rt. 518": {"towns": ["Montgomery"], "note": "In Montgomery, only south of Route 518."},
    "within ¾ mile of a local bus route": {"near_bus_routes": True},
}

DISABILITY_VALUES = {"any", "physical", "visual", "certified"}
WHEELCHAIR_VALUES = {"yes", "no", "with doctor's form"}
SIGN_UP_KINDS = ("none", "on first call", "register first", "certification first", "waitlist")
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")


class RulesSheetError(ValueError):
    pass


def _yes(v: str, field: str, name: str) -> bool:
    v = v.strip().lower()
    if v in ("yes", "y", "true"):
        return True
    if v in ("", "no", "n", "false"):
        return False
    raise RulesSheetError(f"{name}: {field} must be yes or no, not {v!r}")


def _int(v: str, field: str, name: str) -> int | None:
    v = v.strip()
    if not v:
        return None
    if not v.isdigit():
        raise RulesSheetError(f"{name}: {field} must be a whole number, not {v!r}")
    return int(v)


def _area(text: str, name: str) -> dict[str, Any]:
    area: dict[str, Any] = {"text": text, "towns": [], "counties": [], "states": [], "near_bus_routes": False,
                            "notes": []}
    for part in [p.strip() for p in text.split(";") if p.strip()]:
        meaning = AREA_PARTS.get(part.lower())
        if meaning is None:
            raise RulesSheetError(f"{name}: unknown area {part!r}. Add it to AREA_PARTS in ridely/data.py.")
        for k in ("towns", "counties", "states"):
            area[k] += meaning.get(k, [])
        area["near_bus_routes"] |= meaning.get("near_bus_routes", False)
        if meaning.get("note"):
            area["notes"].append(meaning["note"])
    return area


def _sign_up(text: str, name: str) -> str:
    t = text.strip().lower()
    for kind in SIGN_UP_KINDS:
        if t.startswith(kind):
            return kind
    raise RulesSheetError(f"{name}: sign_up {text!r} must start with one of {SIGN_UP_KINDS}")


def _call_by(text: str, name: str) -> str | None:
    t = text.strip()
    if not t:
        return None
    if not re.fullmatch(r"\d{1,2}:\d{2}", t):
        raise RulesSheetError(f"{name}: call_by must look like 16:00, not {t!r}")
    return t


def _also_goes_to(text: str, name: str) -> list[dict[str, str]]:
    from ridely.tools.destinations import lookup_destination

    out = []
    for place in [p.strip() for p in text.split(";") if p.strip()]:
        hit = lookup_destination(place)
        if hit["result"] != "match":
            raise RulesSheetError(f"{name}: also_goes_to {place!r} is not one clear place in data/destinations.yaml. "
                                  "Add it (or an alias) there.")
        out.append({"id": hit["match"]["id"], "name": place})
    return out


def _parse_row(row: dict[str, str]) -> dict[str, Any]:
    name = row["program"].strip()
    if name not in PROGRAM_IDS:
        raise RulesSheetError(f"Unknown program {name!r}. Add it to PROGRAM_IDS in ridely/data.py.")
    dis = row["or_disability"].strip().lower() or None
    if dis is not None and dis not in DISABILITY_VALUES:
        raise RulesSheetError(f"{name}: or_disability must be one of {sorted(DISABILITY_VALUES)}, not {dis!r}")
    wc = row["wheelchair"].strip().lower()
    if wc not in WHEELCHAIR_VALUES:
        raise RulesSheetError(f"{name}: wheelchair must be one of {sorted(WHEELCHAIR_VALUES)}, not {wc!r}")
    phones = [m for m in PHONE_RE.findall(row["phone"])]
    if not phones:
        raise RulesSheetError(f"{name}: no phone number found in the phone column")
    return {
        "id": PROGRAM_IDS[name],
        "name": name,
        "min_age": _int(row["min_age"], "min_age", name),
        "or_disability": dis,
        "needs_medicaid": _yes(row["needs_medicaid"], "needs_medicaid", name),
        "medical_only": _yes(row["medical_only"], "medical_only", name),
        "wheelchair": wc,
        "sign_up": _sign_up(row["sign_up"], name),
        "sign_up_text": row["sign_up"].strip(),
        "notice_business_days": _int(row["notice_business_days"], "notice_business_days", name),
        "call_by": _call_by(row["call_by"], name),
        "max_days_ahead": _int(row["max_days_ahead"], "max_days_ahead", name),
        "ride_hours": row["ride_hours"].strip() or None,
        "area": _area(row["area"], name),
        "also_goes_to": _also_goes_to(row["also_goes_to"], name),
        "cost": row["cost"].strip(),
        "phone": row["phone"].strip(),
        "main_phone": phones[0],
        "book_online": row["book_online"].strip() or None,
        "reply_note": row["reply_note"].strip(),
        "source": row["source"].strip(),
        "row": {k: (v or "").strip() for k, v in row.items()},
    }


@lru_cache
def programs() -> dict[str, dict[str, Any]]:
    """All programs, keyed by id, in the sheet's row order."""
    with (DATA_DIR / "programs.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for row in rows:
        if not (row.get("program") or "").strip():
            continue
        p = _parse_row(row)
        if p["id"] in out:
            raise RulesSheetError(f"Program {p['name']!r} appears twice")
        out[p["id"]] = p
    return out


@lru_cache
def rules_csv_text() -> str:
    return (DATA_DIR / "programs.csv").read_text(encoding="utf-8").strip()


@lru_cache
def destinations() -> list[dict[str, Any]]:
    return yaml.safe_load((DATA_DIR / "destinations.yaml").read_text())


def _who(p: dict) -> str:
    dis = {"any": "any disability", "physical": "a physical disability", "visual": "a vision problem",
           "certified": "Access Link certification"}
    parts = []
    if p["min_age"]:
        parts.append(f"age {p['min_age']}+")
    if p["or_disability"]:
        parts.append(dis[p["or_disability"]])
    who = " or ".join(parts) or "anyone"
    if p["needs_medicaid"]:
        who += ", NJ Medicaid members"
    if p["medical_only"]:
        who += ", medical trips only"
    return who


def program_summaries() -> str:
    """Short text list of programs for the system prompt."""
    lines = []
    for p in programs().values():
        extra = f"; also goes to {', '.join(d['name'] for d in p['also_goes_to'])}" if p["also_goes_to"] else ""
        lines.append(f"- {p['name']} (id: {p['id']}): for {_who(p)}. Area: {p['area']['text']}{extra}. "
                     f"Cost: {p['cost'].rstrip('.')}. Sign-up: {p['sign_up_text']}.")
    return "\n".join(lines)


def phone_numbers_in(text: str) -> set[str]:
    return {re.sub(r"\D", "", m)[-10:] for m in PHONE_RE.findall(text)}


def program_phone_numbers(pid: str) -> set[str]:
    p = programs()[pid]
    return phone_numbers_in(p["phone"] + " " + p["reply_note"])


def known_phone_numbers() -> set[str]:
    """Every phone number that appears in the program data (last 10 digits)."""
    return phone_numbers_in(rules_csv_text())
