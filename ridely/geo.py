"""Turn a street address or a town name into a place (town, county, state).

Street addresses go to the U.S. Census geocoder (free, no key).
Town names ("Princeton", "Lawrenceville") use the table below, because the
Census geocoder does not match a town name alone.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import httpx

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


@dataclass
class Place:
    query: str
    town: str | None  # municipality (Census "county subdivision"), e.g. "Princeton", "Hamilton"
    county: str | None  # e.g. "Mercer"
    state: str | None  # e.g. "NJ"
    matched_address: str | None = None
    lat: float | None = None
    lon: float | None = None
    source: str = "unknown"  # census | town_table | not_found

    def to_dict(self) -> dict:
        return asdict(self)


# Municipality -> county. Keys are lower case. Values are (municipality, county).
_MUNICIPALITIES = {
    # Mercer County (all 12)
    "princeton": ("Princeton", "Mercer"),
    "east windsor": ("East Windsor", "Mercer"),
    "ewing": ("Ewing", "Mercer"),
    "hamilton": ("Hamilton", "Mercer"),
    "hightstown": ("Hightstown", "Mercer"),
    "hopewell": ("Hopewell Township", "Mercer"),
    "hopewell township": ("Hopewell Township", "Mercer"),
    "hopewell borough": ("Hopewell", "Mercer"),
    "lawrence": ("Lawrence", "Mercer"),
    "pennington": ("Pennington", "Mercer"),
    "robbinsville": ("Robbinsville", "Mercer"),
    "trenton": ("Trenton", "Mercer"),
    "west windsor": ("West Windsor", "Mercer"),
    # Neighbors
    "plainsboro": ("Plainsboro", "Middlesex"),
    "south brunswick": ("South Brunswick", "Middlesex"),
    "cranbury": ("Cranbury", "Middlesex"),
    "new brunswick": ("New Brunswick", "Middlesex"),
    "east brunswick": ("East Brunswick", "Middlesex"),
    "monroe": ("Monroe", "Middlesex"),
    "montgomery": ("Montgomery", "Somerset"),
    "franklin": ("Franklin", "Somerset"),
    "hillsborough": ("Hillsborough", "Somerset"),
    "rocky hill": ("Rocky Hill", "Somerset"),
    "bordentown": ("Bordentown", "Burlington"),
    "philadelphia": ("Philadelphia", "Philadelphia"),
}

# Villages and post-office names -> municipality.
_VILLAGES = {
    "princeton junction": "west windsor",
    "lawrenceville": "lawrence",
    "mercerville": "hamilton",
    "hamilton square": "hamilton",
    "yardville": "hamilton",
    "west trenton": "ewing",
    "titusville": "hopewell township",
    "skillman": "montgomery",
    "belle mead": "montgomery",
    "kingston": "south brunswick",  # Kingston is split; most of it is in South Brunswick
    "kendall park": "south brunswick",
    "monmouth junction": "south brunswick",
    "dayton": "south brunswick",
    "griggstown": "franklin",
    "somerset": "franklin",
}

_STATE_OF_COUNTY = {"Philadelphia": "PA"}


def _clean(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\b(nj|new jersey|pa|township|twp|borough|boro|city)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def lookup_town(text: str) -> Place | None:
    """Match a bare town name. Returns None if the text is not a known town."""
    key = _clean(text)
    key = _VILLAGES.get(key, key)
    hit = _MUNICIPALITIES.get(key)
    if not hit:
        return None
    town, county = hit
    return Place(query=text, town=town, county=county, state=_STATE_OF_COUNTY.get(county, "NJ"), source="town_table")




def census_geocode(address: str, client: httpx.Client | None = None, timeout: float = 15.0) -> Place:
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
        "layers": "County Subdivisions,Counties,States",
    }
    own = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        r = client.get(CENSUS_URL, params=params)
        r.raise_for_status()
        matches = r.json()["result"]["addressMatches"]
    finally:
        if own:
            client.close()
    if not matches:
        return Place(query=address, town=None, county=None, state=None, source="not_found")
    m = matches[0]
    g = m.get("geographies", {})
    sub = (g.get("County Subdivisions") or [{}])[0]
    town = sub.get("BASENAME")
    if town == "Hopewell":
        town = "Hopewell Township" if "township" in (sub.get("NAME") or "").lower() else "Hopewell"
    county = (g.get("Counties") or [{}])[0].get("BASENAME")
    state = (g.get("States") or [{}])[0].get("STUSAB")
    return Place(
        query=address,
        town=town,
        county=county,
        state=state,
        matched_address=m.get("matchedAddress"),
        lat=m.get("coordinates", {}).get("y"),
        lon=m.get("coordinates", {}).get("x"),
        source="census",
    )


def resolve_place(text: str, client: httpx.Client | None = None) -> Place:
    """Town name first (fast, offline). Then the Census geocoder for street addresses."""
    town = lookup_town(text)
    if town:
        return town
    # A street address needs a number. Without one, the Census geocoder will not match.
    if not re.search(r"\d", text):
        return Place(query=text, town=None, county=None, state=None, source="not_found")
    try:
        return census_geocode(text, client=client)
    except (httpx.HTTPError, KeyError, ValueError):
        return Place(query=text, town=None, county=None, state=None, source="not_found")
