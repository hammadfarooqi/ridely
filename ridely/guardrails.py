"""Extra checks used in RIDELY_MODE=guardrails.

1. check_reply: runs on every model reply before it is sent. Asks the model to fix the reply if it
   gives a phone number that is not in our data, recommends a program that check_eligibility ruled
   out, or is too long. Removes Markdown.
2. emergency_reply: a fixed 911 message, sent without the model, for clear emergencies.
3. scrub_ids: removes ID-like numbers (SSN, Medicaid IDs) from the user's text before the model
   or the database sees it.
"""

from __future__ import annotations

import json
import re

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturnPart

from ridely.data import known_phone_numbers, programs

PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
MAX_REPLY_CHARS = 1400

# Programs whose phone number is also used as a general help number. Mentioning them is not a recommendation.
_SHARED_HELP_NUMBERS = {"6097519699"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _ruled_out_programs(ctx: RunContext) -> set[str]:
    """Programs that the latest check_eligibility call marked 'no'."""
    latest = None
    for m in ctx.messages:
        for p in m.parts:
            if isinstance(p, ToolReturnPart) and p.tool_name == "check_eligibility":
                latest = p.content
    if latest is None:
        return set()
    if isinstance(latest, str):
        try:
            latest = json.loads(latest)
        except ValueError:
            return set()
    return {r["program_id"] for r in latest.get("programs", []) if r.get("status") == "no"}


def check_reply(ctx: RunContext, output: str) -> str:
    text = output.replace("**", "").replace("__", "")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)

    known = known_phone_numbers()
    found = {_digits(m) for m in PHONE_RE.findall(text)}
    unknown = sorted(n for n in found if n[-10:] not in known)
    if unknown:
        raise ModelRetry(
            f"Your reply has phone number(s) {unknown} that are not in Ridely's program data. "
            "Only give numbers from get_program_details, or 911 / 2-1-1. Rewrite the reply."
        )

    ruled_out = _ruled_out_programs(ctx)
    for pid in ruled_out:
        phone = programs()[pid]["booking"].get("phone")
        if phone and _digits(phone) not in _SHARED_HELP_NUMBERS and _digits(phone) in found:
            raise ModelRetry(
                f"check_eligibility said the rider cannot use {programs()[pid]['name']} (status 'no'), "
                "but your reply gives its phone number. Do not recommend it. Rewrite the reply."
            )

    if len(text) > MAX_REPLY_CHARS:
        raise ModelRetry(f"Your reply is {len(text)} characters. Make it shorter (under 900 characters).")
    return text


_EMERGENCY = re.compile(
    r"\b(chest pain|heart attack|can'?t breathe|cannot breathe|not breathing|trouble breathing|stroke|"
    r"unconscious|passed out|bleeding (a lot|badly|heavily)|overdose|suicid\w*|kill myself|"
    r"dolor de pecho|no puede respirar|no puedo respirar|derrame)\b",
    re.I,
)

EMERGENCY_REPLY = (
    "This sounds like an emergency. Please call 911 now.\n"
    "Esto parece una emergencia. Llame al 911 ahora.\n\n"
    "If you are thinking about hurting yourself, call or text 988."
)


def is_emergency(text: str) -> bool:
    return bool(_EMERGENCY.search(text))


_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_LONG_NUMBER = re.compile(r"\b\d{11,}\b|\b\d{9}\b")  # Medicaid IDs (12 digits), plain SSNs (9 digits)

ID_NOTE = "Note: you do not need to send me ID numbers. I removed the number you sent and did not save it."


def scrub_ids(text: str) -> tuple[str, bool]:
    new = _SSN.sub("[ID number removed]", text)
    new = _LONG_NUMBER.sub("[ID number removed]", new)
    return new, new != text
