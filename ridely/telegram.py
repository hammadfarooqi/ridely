"""Minimal Telegram Bot API client (sendMessage, sendChatAction). Plain text only."""

from __future__ import annotations

import logging
import os

import httpx

# httpx logs each request URL at INFO. Telegram URLs contain the bot token, so keep these logs off.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram limit is 4096 characters


def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return t


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


class TelegramClient:
    def __init__(self, token: str | None = None, http: httpx.AsyncClient | None = None) -> None:
        self.token = token or _token()
        self.http = http or httpx.AsyncClient(timeout=20)

    async def call(self, method: str, **payload) -> dict:
        r = await self.http.post(API.format(token=self.token, method=method), json=payload)
        return r.json()

    async def send_message(self, chat_id: int, text: str) -> None:
        for part in split_message(text):
            await self.call("sendMessage", chat_id=chat_id, text=part, disable_web_page_preview=True)

    async def typing(self, chat_id: int) -> None:
        await self.call("sendChatAction", chat_id=chat_id, action="typing")
