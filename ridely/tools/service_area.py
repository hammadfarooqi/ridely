"""Does a trip's start and end fall inside a program's service area?"""

from __future__ import annotations

from ridely.data import programs
from ridely.geo import Place


def _in_area(area: dict, place: Place) -> bool | None:
    kind = area["kind"]
    if kind == "near_bus_routes":
        return None
    if kind == "state":
        if place.state is None:
            return None
        return place.state in area.get("states", [])
    if place.town is None and place.county is None:
        return None
    towns = area.get("towns") or []
    counties = area.get("counties") or []
    return (place.town in towns) or (place.county in counties)


def check_service_area(program_id: str, start: Place, end: Place, end_destination_id: str | None = None) -> dict:
    prog = programs().get(program_id)
    if prog is None:
        return {"program_id": program_id, "result": "unknown_program"}
    area = prog["area"]
    out = {"program_id": program_id, "name": prog["name"], "start": start.to_dict(), "end": end.to_dict()}

    if area["kind"] == "near_bus_routes":
        out["result"] = "unsure"
        out["reason"] = (
            "Access Link serves trips where both ends are about 3/4 mile or less from an NJ Transit "
            "local bus route, at times the bus runs. Ridely cannot check bus routes. Ask Access Link."
        )
        return out

    s = _in_area(area, start)
    extra = area.get("extra_destinations") or []
    e = True if (end_destination_id and end_destination_id in extra) else _in_area(area, end)

    def label(p: Place) -> str:
        return f"{p.town or '?'}, {p.county or '?'} County, {p.state or '?'}"

    if s is None or e is None:
        out["result"] = "unsure"
        missing = [n for n, v in (("start", s), ("end", e)) if v is None]
        out["reason"] = f"Could not find the town for the {' and '.join(missing)}. Ask for a street address or town."
    elif s and e:
        out["result"] = "in"
        out["reason"] = f"Start ({label(start)}) and end ({label(end)}) are both in the service area."
        if end_destination_id and end_destination_id in extra:
            out["reason"] += " The destination is a listed special stop for this program."
    else:
        out["result"] = "out"
        bad = [n for n, v in (("start", s), ("end", e)) if not v]
        out["reason"] = f"The {' and '.join(bad)} is outside the service area. Start: {label(start)}. End: {label(end)}."
    out["area_description"] = _describe(area)
    if not prog.get("verified", False):
        out["note"] = "Service area is from web sources and is not yet confirmed. Tell the user to confirm when they call."
    return out


def _describe(area: dict) -> str:
    parts = []
    if area.get("towns"):
        parts.append("towns: " + ", ".join(area["towns"]))
    if area.get("counties"):
        parts.append("counties: " + ", ".join(f"{c} County" for c in area["counties"]))
    if area.get("states"):
        parts.append("states: " + ", ".join(area["states"]))
    if area.get("extra_destinations"):
        parts.append("special stops: " + ", ".join(area["extra_destinations"]))
    return "; ".join(parts) or area["kind"]
