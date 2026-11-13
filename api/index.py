"""Vercel entry point: FastAPI app with the Telegram webhook."""

from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402

from ridely.bot import handle_update  # noqa: E402
from ridely.model import model_from_env  # noqa: E402
from ridely.store import get_store  # noqa: E402
from ridely.telegram import TelegramClient  # noqa: E402

app = FastAPI(title="Ridely")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "model": os.environ.get("LLM_MODEL"),
        "has_db": bool(os.environ.get("DATABASE_URL")),
        "has_bot_token": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
    }


@app.post("/api/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret or not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        raise HTTPException(status_code=401, detail="bad secret")
    update = await request.json()
    try:
        model = model_from_env()
    except RuntimeError:
        # Misconfigured: still return 200, or Telegram retries the same update forever.
        model = _NotConfiguredModel()
    status = await handle_update(update, get_store(), TelegramClient(), model)
    return {"ok": True, "status": status}


class _NotConfiguredModel:
    """Placeholder that makes the agent turn fail, so the user gets the fallback message."""

    def __getattr__(self, name):
        raise RuntimeError("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL are not set")
