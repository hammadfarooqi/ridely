"""Match place names like 'RWJ Hamilton' to an address from data/destinations.yaml."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from ridely.data import destinations


def _norm(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(the|hospital|center|centre|medical|nj)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _score(q: str, cand: str) -> float:
    if not q or not cand:
        return 0.0
    if q == cand:
        return 1.0
    qt, ct = set(q.split()), set(cand.split())
    overlap = len(qt & ct) / max(len(qt), 1)
    ratio = SequenceMatcher(None, q, cand).ratio()
    contains = 0.9 if (q in cand or cand in q) and min(len(q), len(cand)) >= 4 else 0.0
    return max(ratio, overlap * 0.95, contains)


def lookup_destination(name: str, limit: int = 3) -> dict:
    from ridely.geo import lookup_town

    town = lookup_town(name)
    if town:  # "Princeton" or "Hamilton" is a town, not a place in our list
        return {"query": name, "result": "is_a_town", "town": town.town, "county": town.county, "state": town.state,
                "message": "This is a town, not a specific place. Ask where in the town, if it matters."}
    q = _norm(name)
    scored = []
    for d in destinations():
        names = [d["name"], d["id"].replace("_", " "), *d.get("aliases", [])]
        best = max(_score(q, _norm(n)) for n in names)
        scored.append((best, d))
    scored.sort(key=lambda x: -x[0])
    top = [(s, d) for s, d in scored[:limit] if s >= 0.6]
    if not top:
        return {"query": name, "result": "no_match",
                "message": "Not in Ridely's list. Ask the user for the street address and town."}

    def fmt(s: float, d: dict) -> dict:
        return {"id": d["id"], "name": d["name"], "address": d["address"], "town": d["town"],
                "county": d["county"], "state": d["state"], "confidence": round(s, 2),
                "address_checked": d.get("geocoded", False)}

    best_s, best = top[0]
    exact_and_unique = best_s == 1.0 and (len(top) == 1 or top[1][0] < 1.0)
    clear_winner = best_s >= 0.85 and (len(top) == 1 or top[1][0] < best_s - 0.1)
    if exact_and_unique or clear_winner:
        return {"query": name, "result": "match", "match": fmt(best_s, best)}
    return {"query": name, "result": "several_possible", "candidates": [fmt(s, d) for s, d in top],
            "message": "Ask the user which one they mean."}
