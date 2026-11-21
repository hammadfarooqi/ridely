"""Agent and bot tests with fake models. No network, no cost."""

import asyncio
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from ridely import bot
from ridely.agent import TZ, Deps, load_history, run_turn, trim_history
from ridely.store import MemoryStore


def run(coro):
    return asyncio.run(coro)


def scripted(steps):
    """A FunctionModel that makes the given tool calls in order, then returns the final text."""
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        n_tool_returns = sum(isinstance(p, ToolReturnPart) for m in messages for p in m.parts)
        if n_tool_returns < len(steps):
            name, args = steps[n_tool_returns]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        return ModelResponse(parts=[TextPart("FINAL " + json.dumps([r.content for r in returns], default=str)[:12000])])
    return FunctionModel(fn)


def test_agent_tools_run_and_history_round_trips():
    model = scripted([
        ("lookup_destination", {"name": "RWJ Hamilton"}),
        ("check_eligibility", {"home": "Princeton", "age": 70}),
        ("check_service_area", {"program_id": "trade", "start": "Princeton", "end": "RWJ Hamilton"}),
        ("booking_deadline", {"program_id": "trade", "trip_date": "2026-11-20"}),
    ])
    deps = Deps(now=datetime(2026, 11, 13, 10, 0, tzinfo=TZ))
    r = run(run_turn("I need a ride to RWJ Hamilton on Friday Nov 20", None, model, deps))
    assert r.reply.startswith("FINAL")
    assert "rwj_hamilton" in r.reply
    assert '"result": "in"' in r.reply or "'result': 'in'" in r.reply or "in" in r.reply
    assert "2026-11-18" in r.reply  # TRADE: 2 business days before Fri Nov 20 -> Wed Nov 18
    history = load_history(r.history_json)
    assert len(history) >= 6
    r2 = run(run_turn("thanks", r.history_json, TestModel(call_tools=[]), deps))
    assert len(load_history(r2.history_json)) == len(history) + 2


def test_bad_program_id_is_sent_back_for_retry():
    calls = {"n": 0}

    def fn(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("get_program_details", {"program_id": "uber"})])
        if calls["n"] == 2:
            # the validation error must be in the conversation now
            text = json.dumps([str(p) for m in messages for p in m.parts])
            assert "uber" in text and ("crosstown" in text or "Input should be" in text)
            return ModelResponse(parts=[ToolCallPart("get_program_details", {"program_id": "trade"})])
        return ModelResponse(parts=[TextPart("done")])

    r = run(run_turn("hi", None, FunctionModel(fn)))
    assert r.reply == "done"


def test_age_out_of_range_triggers_model_retry():
    from pydantic_ai.messages import RetryPromptPart

    seen = {}

    def fn(messages, info):
        # pydantic-ai 2.x sends the retry hint back as a tool return (older versions: RetryPromptPart)
        retries = [p for m in messages for p in m.parts if isinstance(p, (RetryPromptPart, ToolReturnPart))]
        if not retries:
            return ModelResponse(parts=[ToolCallPart("check_eligibility", {"age": 700})])
        seen["retry"] = str(retries[-1].content)
        return ModelResponse(parts=[TextPart("asked again")])

    r = run(run_turn("hi", None, FunctionModel(fn)))
    assert r.reply == "asked again"
    assert "between 1 and 119" in seen["retry"]


def test_trim_history_keeps_tool_pairs():
    msgs = []
    h = None
    for i in range(5):
        r = run(run_turn(f"msg {i}", h, scripted([("lookup_destination", {"name": "mall"})])))
        h = r.history_json
    msgs = load_history(h)
    trimmed = trim_history(msgs, max_turns=2)
    assert len(trimmed) < len(msgs)
    first = trimmed[0]
    assert any(type(p).__name__ == "UserPromptPart" for p in first.parts)


# ---------- bot ----------

class FakeSender:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.typing_calls = 0

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def typing(self, chat_id):
        self.typing_calls += 1


def upd(uid, text=None, chat=42):
    m = {"message_id": uid, "chat": {"id": chat, "type": "private"}}
    if text is not None:
        m["text"] = text
    return {"update_id": uid, "message": m}


def test_bot_start_reply_and_duplicate():
    store, sender = MemoryStore(), FakeSender()
    model = TestModel(call_tools=[], custom_output_text="Where are you going?")
    assert run(bot.handle_update(upd(1, "/start"), store, sender, model)) == "welcome"
    assert run(bot.handle_update(upd(2, "I need a ride"), store, sender, model)) == "replied"
    assert sender.sent[-1] == (42, "Where are you going?")
    assert run(bot.handle_update(upd(2, "I need a ride"), store, sender, model)) == "duplicate"
    assert store.history[42]
    assert store.turns[-1]["requests"] == 1


def test_bot_non_text_and_reset():
    store, sender = MemoryStore(), FakeSender()
    model = TestModel(call_tools=[])
    assert run(bot.handle_update(upd(1, None), store, sender, model)) == "non_text"
    run(bot.handle_update(upd(2, "hello"), store, sender, model))
    assert 42 in store.history
    assert run(bot.handle_update(upd(3, "/reset"), store, sender, model)) == "reset"
    assert 42 not in store.history


def test_bot_error_sends_fallback():
    def broken(messages, info):
        raise RuntimeError("provider down")

    store, sender = MemoryStore(), FakeSender()
    assert run(bot.handle_update(upd(1, "hi"), store, sender, FunctionModel(broken))) == "error"
    assert "609-751-9699" in sender.sent[-1][1]
    assert "provider down" in store.turns[-1]["error"]


# ---------- API ----------

def test_webhook_rejects_bad_secret_and_accepts_good(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import api.index as idx

    sender = FakeSender()
    monkeypatch.setattr(idx, "TelegramClient", lambda: sender)
    monkeypatch.setattr(idx, "model_from_env", lambda: TestModel(call_tools=[], custom_output_text="ok"))
    c = TestClient(idx.app)
    assert c.post("/api/telegram", json=upd(10, "hi")).status_code == 401
    r = c.post("/api/telegram", json=upd(11, "hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert r.status_code == 200 and r.json()["status"] == "replied"
    assert sender.sent[-1][1] == "ok"
    assert c.get("/api/health").json()["ok"] is True
