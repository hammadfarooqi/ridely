"""Does a trip's start and end fall inside a program's service area (the sheet's area + also_goes_to)?"""

from __future__ import annotations

from ridely.data import programs
from ridely.geo import Place


def _in_area(area: dict, place: Place) -> bool | None:
    if place.town is None and place.county is None and place.state is None:
        return None
    return (place.town in area["towns"]) or (place.county in area["counties"]) or (place.state in area["states"])


def check_service_area(program_id: str, start: Place, end: Place, end_destination_id: str | None = None) -> dict:
    prog = programs().get(program_id)
    if prog is None:
        return {"program_id": program_id, "result": "unknown_program"}
    area = prog["area"]
    out = {"program_id": program_id, "name": prog["name"], "start": start.to_dict(), "end": end.to_dict(),
           "area": area["text"]}
    if prog["also_goes_to"]:
        out["also_goes_to"] = [d["name"] for d in prog["also_goes_to"]]

    if area["near_bus_routes"]:
        out["result"] = "unsure"
        out["reason"] = ("Access Link serves trips where both ends are within about 3/4 mile of a local bus route, "
                         "at times the bus runs. Ridely cannot check bus routes. The rider can ask Access Link.")
        return out

    special = {d["id"] for d in prog["also_goes_to"]}
    s = _in_area(area, start)
    to_special = bool(end_destination_id and end_destination_id in special)
    e = True if to_special else _in_area(area, end)

    def label(p: Place) -> str:
        return f"{p.town or '?'}, {p.county or '?'} County, {p.state or '?'}"

    if s is None or e is None:
        out["result"] = "unsure"
        missing = [n for n, v in (("start", s), ("end", e)) if v is None]
        out["reason"] = f"Could not find the town for the {' and '.join(missing)}. Ask for a street address or town."
    elif s and e:
        out["result"] = "in"
        out["reason"] = f"Start ({label(start)}) and end ({label(end)}) are both in the service area."
        if to_special:
            out["reason"] += " The destination is a listed extra stop (also_goes_to) for this program."
    else:
        out["result"] = "out"
        bad = [n for n, v in (("start", s), ("end", e)) if not v]
        out["reason"] = f"The {' and '.join(bad)} is outside the service area. Start: {label(start)}. End: {label(end)}."
    if area["notes"] and out["result"] == "in":
        out["note"] = " ".join(area["notes"])
    return out
