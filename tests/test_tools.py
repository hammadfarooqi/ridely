from datetime import date

import httpx
import pytest

from ridely.geo import Place, census_geocode, lookup_town, resolve_place
from ridely.tools.deadline import booking_deadline, federal_holidays, is_business_day
from ridely.tools.destinations import lookup_destination
from ridely.tools.eligibility import Person, check_eligibility
from ridely.tools.service_area import check_service_area

PRINCETON = Place(query="Princeton", town="Princeton", county="Mercer", state="NJ")
HAMILTON = Place(query="Hamilton", town="Hamilton", county="Mercer", state="NJ")
PLAINSBORO = Place(query="Plainsboro", town="Plainsboro", county="Middlesex", state="NJ")
NEW_BRUNSWICK = Place(query="New Brunswick", town="New Brunswick", county="Middlesex", state="NJ")
PHILLY = Place(query="Philadelphia", town="Philadelphia", county="Philadelphia", state="PA")
UNKNOWN = Place(query="??", town=None, county=None, state=None, source="not_found")


def status(results, pid):
    return next(r for r in results if r["program_id"] == pid)["status"]


# ---------- deadline ----------

def test_holidays_2026():
    h = federal_holidays(2026)
    assert date(2026, 11, 26) in h  # Thanksgiving
    assert date(2026, 7, 3) in h  # July 4 is a Saturday -> observed Friday
    assert date(2026, 12, 25) in h


def test_business_days():
    assert is_business_day(date(2026, 11, 13))  # Friday
    assert not is_business_day(date(2026, 11, 14))  # Saturday
    assert not is_business_day(date(2026, 11, 26))  # Thanksgiving


def test_medicaid_two_business_days_skips_thanksgiving():
    r = booking_deadline("medicaid_nemt", date(2026, 11, 30), today=date(2026, 11, 13))
    assert r["last_day_to_call"] == "2026-11-25"
    assert r["result"] == "ok"


def test_trade_too_early_and_too_late():
    assert booking_deadline("trade", date(2026, 12, 31), today=date(2026, 11, 13))["result"] == "too_early"
    late = booking_deadline("trade", date(2026, 11, 16), today=date(2026, 11, 13))
    assert late["result"] == "too_late"


def test_call_today():
    r = booking_deadline("medicaid_nemt", date(2026, 11, 17), today=date(2026, 11, 13))
    assert r["last_day_to_call"] == "2026-11-13"
    assert r["result"] == "call_today"


def test_no_booking_and_past_trip():
    assert booking_deadline("muni_transit", date(2026, 11, 20), date(2026, 11, 13))["result"] == "no_booking_needed"
    assert booking_deadline("crosstown", date(2026, 11, 1), date(2026, 11, 13))["result"] == "trip_in_past"


# ---------- eligibility ----------

def test_70_year_old_in_princeton():
    r = check_eligibility(Person(age=70, home=PRINCETON))
    assert status(r, "crosstown") == "likely"
    assert status(r, "rideprovide") == "likely"
    assert status(r, "trade") == "likely"
    assert status(r, "medicaid_nemt") == "maybe"
    assert status(r, "access_link") == "maybe"


def test_62_year_old_in_hamilton():
    r = check_eligibility(Person(age=62, home=HAMILTON, has_disability=False, visually_impaired=False))
    assert status(r, "trade") == "likely"
    assert status(r, "rideprovide") == "no"
    assert status(r, "crosstown") == "no"
    assert status(r, "access_link") == "no"


def test_40_year_old_wheelchair_user_princeton():
    r = check_eligibility(Person(age=40, has_disability=True, home=PRINCETON))
    assert status(r, "trade") == "likely"
    assert status(r, "access_link") == "maybe"
    assert status(r, "rideprovide") == "maybe"  # could still be visually impaired: unknown


def test_visually_impaired_any_age_rideprovide():
    r = check_eligibility(Person(age=30, visually_impaired=True, home=PLAINSBORO))
    assert status(r, "rideprovide") == "likely"
    assert status(r, "trade") == "no"  # not Mercer


def test_missing_age_and_home_is_maybe():
    r = check_eligibility(Person())
    assert status(r, "trade") == "maybe"
    trade = next(x for x in r if x["program_id"] == "trade")
    assert "age" in trade["missing_info"] and "home town" in trade["missing_info"]


def test_medicaid_rules():
    assert status(check_eligibility(Person(has_medicaid=True, trip_is_medical=True)), "medicaid_nemt") == "likely"
    assert status(check_eligibility(Person(has_medicaid=True, trip_is_medical=False)), "medicaid_nemt") == "no"
    assert status(check_eligibility(Person(has_medicaid=False)), "medicaid_nemt") == "no"


# ---------- service area ----------

def test_crosstown_to_penn_medicine_is_special_stop():
    r = check_service_area("crosstown", PRINCETON, PLAINSBORO, end_destination_id="penn_medicine_princeton")
    assert r["result"] == "in"


def test_crosstown_to_hamilton_is_out_but_trade_is_in():
    assert check_service_area("crosstown", PRINCETON, HAMILTON)["result"] == "out"
    assert check_service_area("trade", PRINCETON, HAMILTON)["result"] == "in"
    assert check_service_area("rideprovide", PRINCETON, PLAINSBORO)["result"] == "in"


def test_trade_to_new_brunswick_out_and_medicaid_to_philly_out():
    assert check_service_area("trade", PRINCETON, NEW_BRUNSWICK)["result"] == "out"
    assert check_service_area("medicaid_nemt", PRINCETON, PHILLY)["result"] == "out"
    assert check_service_area("medicaid_nemt", PRINCETON, NEW_BRUNSWICK)["result"] == "in"


def test_unknown_place_and_access_link_are_unsure():
    assert check_service_area("trade", PRINCETON, UNKNOWN)["result"] == "unsure"
    assert check_service_area("access_link", PRINCETON, HAMILTON)["result"] == "unsure"


# ---------- destinations ----------

@pytest.mark.parametrize("q,expected", [
    ("RWJ Hamilton", "rwj_hamilton"),
    ("princeton hospital", "penn_medicine_princeton"),
    ("Princeton Medical Center", "penn_medicine_princeton"),
    ("the senior center", "cmap"),
    ("McCaffreys", "princeton_shopping_center"),
    ("whole foods", "whole_foods_princeton"),
])
def test_destination_matches(q, expected):
    r = lookup_destination(q)
    assert r["result"] == "match" and r["match"]["id"] == expected


@pytest.mark.parametrize("q", ["capital health", "rwj", "robert wood johnson"])
def test_ambiguous_destinations_ask(q):
    assert lookup_destination(q)["result"] == "several_possible"


def test_unknown_destination():
    assert lookup_destination("dentist on nassau street")["result"] == "no_match"


# ---------- geo ----------

@pytest.mark.parametrize("q,town,county", [
    ("Princeton", "Princeton", "Mercer"),
    ("Princeton, NJ", "Princeton", "Mercer"),
    ("Lawrenceville", "Lawrence", "Mercer"),
    ("Princeton Junction", "West Windsor", "Mercer"),
    ("Plainsboro Township", "Plainsboro", "Middlesex"),
    ("hopewell twp", "Hopewell Township", "Mercer"),
])
def test_town_table(q, town, county):
    p = lookup_town(q)
    assert p and p.town == town and p.county == county


def test_census_geocode_parsing():
    payload = {"result": {"addressMatches": [{
        "matchedAddress": "1 HAMILTON HEALTH PL, HAMILTON, NJ, 08690",
        "coordinates": {"x": -74.67, "y": 40.21},
        "geographies": {
            "County Subdivisions": [{"BASENAME": "Hamilton", "NAME": "Hamilton township"}],
            "Counties": [{"BASENAME": "Mercer"}],
            "States": [{"STUSAB": "NJ"}],
        }}]}}
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    p = census_geocode("1 Hamilton Health Pl, Hamilton NJ", client=client)
    assert (p.town, p.county, p.state, p.source) == ("Hamilton", "Mercer", "NJ", "census")


def test_resolve_place_without_number_does_not_call_census():
    def boom(req):  # pragma: no cover
        raise AssertionError("should not call the network")
    client = httpx.Client(transport=httpx.MockTransport(boom))
    assert resolve_place("somewhere nice", client=client).source == "not_found"
