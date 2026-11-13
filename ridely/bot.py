"""Handle one Telegram update: commands, the agent turn, storage, and errors."""

from __future__ import annotations

import logging
import os
import time

from pydantic_ai.models import Model

from ridely.agent import current_mode, run_turn
from ridely.store import Store

log = logging.getLogger("ridely")

WELCOME = (
    "Hi, I am Ridely. I help older adults and people with disabilities in the Princeton area "
    "find free or low-cost rides, for example to a doctor.\n\n"
    "Tell me about your trip: where you are going, when, and where you start from.\n\n"
    "Hola, también hablo español.\n\n"
    "I do not book rides. I tell you who to call and what to say. "
    "In an emergency, call 911. Send /reset to start over."
)

FALLBACK = (
    "Sorry, something went wrong on my side. Please try again in a minute. "
    "For help now, call the Center for Modern Aging Princeton at 609-751-9699, or dial 2-1-1."
)

NON_TEXT = "Sorry, I can only read text messages. Please type your question."


class Sender:
    """What the bot needs from a channel. TelegramClient matches this."""

    async def send_message(self, chat_id: int, text: str) -> None: ...
    async def typing(self, chat_id: int) -> None: ...


async def handle_update(update: dict, store: Store, sender: Sender, model: Model) -> str:
    """Returns a short status string (for logs and tests)."""
    update_id = update.get("update_id")
    if update_id is None:
        return "no_update_id"
    if not await store.first_time_seen(update_id):
        return "duplicate"

    msg = update.get("message") or update.get("edited_message")
    if not msg or "chat" not in msg:
        return "ignored"
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    if not text:
        await sender.send_message(chat_id, NON_TEXT)
        return "non_text"
    cmd = text.split()[0].lower().split("@")[0] if text.startswith("/") else None
    if cmd in ("/start", "/help"):
        if cmd == "/start":
            await store.reset(chat_id)
        await sender.send_message(chat_id, WELCOME)
        return "welcome"
    if cmd == "/reset":
        await store.reset(chat_id)
        await sender.send_message(chat_id, "OK, I forgot our chat. Tell me about your trip.")
        return "reset"
    if len(text) > 2000:
        text = text[:2000]

    id_note = None
    if current_mode() == "guardrails":
        from ridely.guardrails import EMERGENCY_REPLY, ID_NOTE, is_emergency, scrub_ids

        if is_emergency(text):
            await sender.send_message(chat_id, EMERGENCY_REPLY)
            return "emergency"
        text, removed = scrub_ids(text)
        id_note = ID_NOTE if removed else None

    await sender.typing(chat_id)
    t0 = time.monotonic()
    try:
        history = await store.get_history(chat_id)
        result = await run_turn(text, history, model)
    except Exception as e:  # never leave the user without a reply
        log.exception("agent turn failed")
        await sender.send_message(chat_id, FALLBACK)
        await store.log_turn(chat_id, model=os.environ.get("LLM_MODEL"), error=f"{type(e).__name__}: {e}"[:500],
                             latency_ms=int((time.monotonic() - t0) * 1000))
        return "error"

    await store.save_history(chat_id, result.history_json)
    reply = result.reply or FALLBACK
    if id_note:
        reply = f"{id_note}\n\n{reply}"
    await sender.send_message(chat_id, reply)
    await store.log_turn(chat_id, model=os.environ.get("LLM_MODEL"), requests=result.requests,
                         input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                         latency_ms=int((time.monotonic() - t0) * 1000))
    return "replied"
