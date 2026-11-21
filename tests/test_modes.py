"""Tests for RIDELY_MODE=minimal and guardrails. Fake models only."""

import asyncio

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from ridely import bot
from ridely.agent import AGENTS, run_turn
from ridely.guardrails import is_emergency, scrub_ids
from ridely.store import MemoryStore


def run(c):
    return asyncio.run(c)


def test_minimal_mode_has_no_tools_and_all_facts():
    seen = {}

    def fn(messages, info):
        seen["tools"] = [t.name for t in info.function_tools]
        seen["instructions"] = messages[-1].instructions or ""
        return ModelResponse(parts=[TextPart("ok")])

    run(run_turn("hi", None, FunctionModel(fn), mode="minimal"))
    assert seen["tools"] == []
    assert "609-452-5144" in seen["instructions"] and "RULES (CSV):" in seen["instructions"]
    assert "program,min_age,or_disability" in seen["instructions"]  # the sheet is pasted in


def test_standard_mode_has_six_tools():
    seen = {}

    def fn(messages, info):
        seen["tools"] = sorted(t.name for t in info.function_tools)
        return ModelResponse(parts=[TextPart("ok")])

    run(run_turn("hi", None, FunctionModel(fn), mode="standard"))
    assert len(seen["tools"]) == 6


def _retry_texts(messages):
    return [str(p.content) for m in messages for p in m.parts
            if type(p).__name__ in ("RetryPromptPart",) or (isinstance(p, ToolReturnPart) and "Rewrite" in str(p.content))]


def test_guardrails_rejects_made_up_phone_then_accepts_fix():
    calls = {"n": 0, "retry": ""}

    def fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[TextPart("Call **Uber Health** at 800-555-0199.")])
        calls["retry"] = " ".join(str(getattr(p, "content", "")) for m in messages for p in m.parts)
        return ModelResponse(parts=[TextPart("Call **TRADE** at 609-530-1971.")])

    r = run(run_turn("hi", None, FunctionModel(fn), mode="guardrails"))
    assert "8005550199" in calls["retry"]
    assert r.reply == "Call TRADE at 609-530-1971."  # Markdown removed


def test_guardrails_blocks_ruled_out_program():
    calls = {"n": 0}

    def fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:  # 40-year-old in Hamilton, no Medicaid -> Medicaid rides are 'no'
            return ModelResponse(parts=[ToolCallPart("check_eligibility", {"home": "Hamilton", "age": 40,
                                                                           "has_medicaid": False})])
        if calls["n"] == 2:
            return ModelResponse(parts=[TextPart("Try Medicaid rides at 1-866-527-9933.")])
        return ModelResponse(parts=[TextPart("Try TRADE at 609-530-1971.")])

    r = run(run_turn("hi", None, FunctionModel(fn), mode="guardrails"))
    assert calls["n"] == 3 and "609-530-1971" in r.reply


def test_guardrails_allows_number_shared_with_a_fitting_program():
    calls = {"n": 0}

    def fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:  # 70 in Princeton, wheelchair: Crosstown 'no', but... and RideProvide 'no'
            return ModelResponse(parts=[ToolCallPart("check_eligibility", {"home": "Princeton", "age": 70,
                                                                           "uses_wheelchair": False})])
        return ModelResponse(parts=[TextPart("Crosstown: book at 609-452-5144.")])  # RideProvide is future_only

    r = run(run_turn("hi", None, FunctionModel(fn), mode="guardrails"))
    assert calls["n"] == 2 and "609-452-5144" in r.reply


def test_guardrails_rejects_long_reply():
    calls = {"n": 0}

    def fn(messages, info):
        calls["n"] += 1
        return ModelResponse(parts=[TextPart("x " * 1000 if calls["n"] == 1 else "short")])

    assert run(run_turn("hi", None, FunctionModel(fn), mode="guardrails")).reply == "short"


def test_emergency_and_scrub():
    assert is_emergency("my husband has chest pain")
    assert is_emergency("mi esposo tiene dolor de pecho")
    assert not is_emergency("I need a ride to the doctor for my chest x-ray")
    t, removed = scrub_ids("my medicaid id is 123456789012 and ssn 123-45-6789, call me at 609-555-1234")
    assert removed and "123456789012" not in t and "123-45-6789" not in t and "609-555-1234" in t


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append(text)

    async def typing(self, chat_id):
        pass


def test_bot_guardrails_mode(monkeypatch):
    monkeypatch.setenv("RIDELY_MODE", "guardrails")
    store, sender = MemoryStore(), FakeSender()
    u = lambda i, t: {"update_id": i, "message": {"message_id": i, "chat": {"id": 7}, "text": t}}
    assert run(bot.handle_update(u(1, "he can't breathe, need hospital"), store, sender, TestModel())) == "emergency"
    assert "911" in sender.sent[-1]
    run(bot.handle_update(u(2, "my id is 123456789012"), store, sender, TestModel(call_tools=[], custom_output_text="ok")))
    assert sender.sent[-1].startswith("Note: you do not need")
    assert b"123456789012" not in store.history[7]


def test_agents_built_for_all_modes():
    assert set(AGENTS) == {"minimal", "standard", "guardrails"}
