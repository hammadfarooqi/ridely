"""The Ridely agent: one PydanticAI Agent with read-only tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from ridely import data
from ridely.geo import Place, resolve_place
from ridely.prompt import static_instructions
from ridely.tools.deadline import booking_deadline as _deadline
from ridely.tools.destinations import lookup_destination as _lookup
from ridely.tools.eligibility import Person, check_eligibility as _eligibility
from ridely.tools.service_area import check_service_area as _service_area

TZ = ZoneInfo("America/New_York")
ProgramId = Literal["medicaid_nemt", "trade", "crosstown", "access_link", "rideprovide"]
assert set(ProgramId.__args__) == set(data.programs()), "ProgramId must list every program in data/programs.csv"

MAX_MODEL_REQUESTS = 8  # stops endless tool loops in one turn
MAX_TURNS_KEPT = 12  # user turns of history sent to the model


@dataclass
class Deps:
    now: datetime = field(default_factory=lambda: datetime.now(TZ))
    http: httpx.Client | None = None

    @property
    def today(self) -> date:
        return self.now.date()


def _today(ctx: RunContext[Deps]) -> str:
    # Kept apart from the static text so the static part can be cached.
    return f"Today is {ctx.deps.now.strftime('%A, %B %d, %Y')}. Time now: {ctx.deps.now.strftime('%I:%M %p')} (New Jersey time)."


def _place(ctx: RunContext[Deps], text: str) -> tuple[Place, str | None]:
    """Resolve a destination name, a town, or a street address. Returns (place, destination_id)."""
    hit = _lookup(text)
    if hit["result"] == "match":
        m = hit["match"]
        return Place(query=text, town=m["town"], county=m["county"], state=m["state"],
                     matched_address=m["address"], source="destination_list"), m["id"]
    return resolve_place(text, client=ctx.deps.http), None


def get_program_details(ctx: RunContext[Deps], program_id: ProgramId) -> dict:
    """Facts for one program from the rules sheet: area, extra stops, cost, phone, online booking, ride hours,
    sign-up, wheelchair, notice, and a note to pass on to the rider.

    Args:
        program_id: One of the program ids from the program list.
    """
    p = data.programs()[program_id]
    return {
        "name": p["name"], "cost": p["cost"], "phone": p["phone"], "book_online": p["book_online"],
        "area": p["area"]["text"], "also_goes_to": [d["name"] for d in p["also_goes_to"]],
        "ride_hours": p["ride_hours"], "wheelchair": p["wheelchair"], "sign_up": p["sign_up_text"],
        "notice_business_days": p["notice_business_days"], "call_by": p["call_by"],
        "max_days_ahead": p["max_days_ahead"], "reply_note": p["reply_note"], "source": p["source"],
    }


def check_eligibility(
    ctx: RunContext[Deps],
    home: str | None = None,
    age: int | None = None,
    physical_disability: bool | None = None,
    visually_impaired: bool | None = None,
    other_disability: bool | None = None,
    access_link_certified: bool | None = None,
    uses_wheelchair: bool | None = None,
    has_medicaid: bool | None = None,
    trip_is_medical: bool | None = None,
) -> dict:
    """Check which ride programs the rider can use. Leave a value empty (null) if you do not know it yet.

    Args:
        home: The rider's home town or street address, e.g. "Princeton" or "45 Stockton St, Princeton NJ".
        age: Rider's age in years.
        physical_disability: True if the rider has a physical disability (trouble walking, uses a walker, etc.).
        visually_impaired: True if the rider is blind or has low vision.
        other_disability: True if the rider has another disability that makes travel hard.
        access_link_certified: True if the rider is already certified (approved) for NJ Transit Access Link.
        uses_wheelchair: True if the rider uses a wheelchair.
        has_medicaid: True if the rider has NJ Medicaid / NJ FamilyCare.
        trip_is_medical: True if the trip is to a doctor, hospital, clinic, dialysis, therapy or similar.
    """
    if age is not None and not (0 < age < 120):
        raise ModelRetry("Age must be a number of years between 1 and 119. Ask the user if unsure.")
    place = resolve_place(home, client=ctx.deps.http) if home else None
    if home and place and place.town is None and place.state is None:
        place = None
    person = Person(age=age, physical_disability=physical_disability, visually_impaired=visually_impaired,
                    other_disability=other_disability, access_link_certified=access_link_certified,
                    uses_wheelchair=uses_wheelchair, has_medicaid=has_medicaid, trip_is_medical=trip_is_medical,
                    home=place)
    out = {"home_resolved": place.to_dict() if place else None, "programs": _eligibility(person)}
    if home and place is None:
        out["note"] = f"Could not find the town for '{home}'. Ask for the town name."
    return out


def lookup_destination(ctx: RunContext[Deps], name: str) -> dict:
    """Find the address and town of a place by name, e.g. "RWJ Hamilton" or "Princeton hospital".

    Args:
        name: The place name the user gave.
    """
    return _lookup(name)


def geocode_address(ctx: RunContext[Deps], address: str) -> dict:
    """Find the town, county and state for a street address or a town name.

    Args:
        address: A street address with number, or a town name.
    """
    return resolve_place(address, client=ctx.deps.http).to_dict()


def check_service_area(ctx: RunContext[Deps], program_id: ProgramId, start: str, end: str) -> dict:
    """Check if a trip's start and end are inside a program's service area.

    Args:
        program_id: The program to check.
        start: Where the trip starts: a town, street address, or place name.
        end: Where the trip goes: a town, street address, or place name.
    """
    s, _ = _place(ctx, start)
    e, dest_id = _place(ctx, end)
    return _service_area(program_id, s, e, end_destination_id=dest_id)


def booking_deadline(ctx: RunContext[Deps], program_id: ProgramId, trip_date: date,
                     trip_time: time | None = None) -> dict:
    """The last day and time to call to book a ride with a program, for a trip on trip_date.
    Also checks the trip time against the program's ride hours.

    Args:
        program_id: The program.
        trip_date: The day of the trip, as YYYY-MM-DD. Work out the date from words like "Friday" using today's date.
        trip_time: The pickup or appointment time as HH:MM (24-hour), if the user said it.
    """
    if (trip_date - ctx.deps.today).days > 366:
        raise ModelRetry("That date is more than a year away. Check the year.")
    return _deadline(program_id, trip_date, ctx.deps.today, now=ctx.deps.now.time(), trip_time=trip_time)


# ---------- agents (modes) ----------
#
# RIDELY_MODE picks one:
#   minimal    - Cyron's prompt: no tools. The rules sheet is in the prompt; the model decides everything.
#   standard   - 6 read-only tools; code decides eligibility, area and deadlines. (default)
#   guardrails - standard + reply checks (phone numbers only from our data, no program the
#                eligibility tool ruled out, short replies) + emergency/ID-number filters in the bot.

TOOLS = [get_program_details, check_eligibility, lookup_destination, geocode_address,
         check_service_area, booking_deadline]
MODES = ("minimal", "standard", "guardrails")


def _make_agent(mode: str) -> Agent[Deps, str]:
    if mode == "minimal":
        from ridely.prompt import minimal_instructions

        a = Agent(None, deps_type=Deps, instructions=[minimal_instructions(), _today], retries=2)
    else:
        a = Agent(None, deps_type=Deps, instructions=[static_instructions(), _today], tools=TOOLS, retries=2)
    if mode == "guardrails":
        from ridely.guardrails import check_reply

        a.output_validator(check_reply)
    return a


AGENTS = {m: _make_agent(m) for m in MODES}


def current_mode() -> str:
    import os

    m = os.environ.get("RIDELY_MODE", "standard").strip().lower()
    return m if m in MODES else "standard"


# ---------- running one turn ----------

@dataclass
class TurnResult:
    reply: str
    history_json: bytes
    requests: int
    input_tokens: int
    output_tokens: int


def trim_history(messages: list[ModelMessage], max_turns: int = MAX_TURNS_KEPT) -> list[ModelMessage]:
    """Keep the last max_turns user turns. Cuts only where a user message starts, so tool calls stay paired."""
    starts = [i for i, m in enumerate(messages)
              if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)]
    if len(starts) <= max_turns:
        return messages
    return messages[starts[-max_turns]:]


def load_history(history_json: bytes | str | None) -> list[ModelMessage]:
    if not history_json:
        return []
    return list(ModelMessagesTypeAdapter.validate_json(history_json))


async def run_turn(user_text: str, history_json: bytes | str | None, model: Model, deps: Deps | None = None,
                   mode: str | None = None) -> TurnResult:
    deps = deps or Deps()
    history = trim_history(load_history(history_json))
    result = await AGENTS[mode or current_mode()].run(
        user_text,
        model=model,
        deps=deps,
        message_history=history,
        usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
    )
    u = result.usage
    return TurnResult(
        reply=str(result.output).strip(),
        history_json=ModelMessagesTypeAdapter.dump_json(result.all_messages()),
        requests=u.requests,
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
    )
