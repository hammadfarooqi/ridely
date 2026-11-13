"""The Ridely agent: one PydanticAI Agent with read-only tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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
ProgramId = Literal["crosstown", "rideprovide", "trade", "access_link", "medicaid_nemt", "muni_transit"]
assert set(ProgramId.__args__) == set(data.programs()), "ProgramId must list every program in programs.yaml"

MAX_MODEL_REQUESTS = 8  # stops endless tool loops in one turn
MAX_TURNS_KEPT = 12  # user turns of history sent to the model


@dataclass
class Deps:
    now: datetime = field(default_factory=lambda: datetime.now(TZ))
    http: httpx.Client | None = None

    @property
    def today(self) -> date:
        return self.now.date()


agent = Agent(
    None,  # model is passed per run (real model, or a test model)
    deps_type=Deps,
    instructions=static_instructions(),
    retries=2,
)


@agent.instructions
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


@agent.tool(retries=2)
def get_program_details(ctx: RunContext[Deps], program_id: ProgramId) -> dict:
    """Full facts for one program: who can use it, area, cost, phone, hours, notice, how to register, what to say.

    Args:
        program_id: One of the program ids from the program list.
    """
    p = dict(data.programs()[program_id])
    p.pop("eligibility", None)  # eligibility is decided by check_eligibility
    return p


@agent.tool(retries=2)
def check_eligibility(
    ctx: RunContext[Deps],
    home: str | None = None,
    age: int | None = None,
    has_disability: bool | None = None,
    visually_impaired: bool | None = None,
    has_medicaid: bool | None = None,
    trip_is_medical: bool | None = None,
) -> dict:
    """Check which ride programs the rider can use. Leave a value empty (null) if you do not know it yet.

    Args:
        home: The rider's home town or street address, e.g. "Princeton" or "45 Stockton St, Princeton NJ".
        age: Rider's age in years.
        has_disability: True if the rider has a disability that makes travel or using the bus hard.
        visually_impaired: True if the rider is blind or has low vision.
        has_medicaid: True if the rider has NJ Medicaid / NJ FamilyCare.
        trip_is_medical: True if the trip is to a doctor, hospital, clinic, dialysis, therapy or similar.
    """
    if age is not None and not (0 < age < 120):
        raise ModelRetry("Age must be a number of years between 1 and 119. Ask the user if unsure.")
    place = resolve_place(home, client=ctx.deps.http) if home else None
    if home and place and place.town is None:
        place = None
    results = _eligibility(Person(age=age, visually_impaired=visually_impaired, has_disability=has_disability,
                                  has_medicaid=has_medicaid, trip_is_medical=trip_is_medical, home=place))
    out = {"home_resolved": place.to_dict() if place else None, "programs": results}
    if home and place is None:
        out["note"] = f"Could not find the town for '{home}'. Ask for the town name."
    return out


@agent.tool(retries=2)
def lookup_destination(ctx: RunContext[Deps], name: str) -> dict:
    """Find the address and town of a place by name, e.g. "RWJ Hamilton" or "Princeton hospital".

    Args:
        name: The place name the user gave.
    """
    return _lookup(name)


@agent.tool(retries=2)
def geocode_address(ctx: RunContext[Deps], address: str) -> dict:
    """Find the town, county and state for a street address or a town name.

    Args:
        address: A street address with number, or a town name.
    """
    return resolve_place(address, client=ctx.deps.http).to_dict()


@agent.tool(retries=2)
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


@agent.tool(retries=2)
def booking_deadline(ctx: RunContext[Deps], program_id: ProgramId, trip_date: date) -> dict:
    """The last day to call to book a ride with a program, for a trip on trip_date.

    Args:
        program_id: The program.
        trip_date: The day of the trip, as YYYY-MM-DD. Work out the date from words like "Friday" using today's date.
    """
    if (trip_date - ctx.deps.today).days > 366:
        raise ModelRetry("That date is more than a year away. Check the year.")
    return _deadline(program_id, trip_date, ctx.deps.today)


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


async def run_turn(user_text: str, history_json: bytes | str | None, model: Model, deps: Deps | None = None) -> TurnResult:
    deps = deps or Deps()
    history = trim_history(load_history(history_json))
    result = await agent.run(
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
