"""One-time setup helpers.

    uv run python scripts/setup.py init-db                 # create tables in Neon (DATABASE_URL)
    uv run python scripts/setup.py set-webhook <base_url>  # point Telegram at https://<base_url>/api/telegram
    uv run python scripts/setup.py webhook-info
    uv run python scripts/setup.py bot-profile             # set bot description and command list
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

load_dotenv()

API = "https://api.telegram.org/bot{token}/{method}"


def tg(method: str, **payload) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = httpx.post(API.format(token=token, method=method), json=payload, timeout=20)
    return r.json()


def init_db() -> None:
    from ridely.store import PostgresStore

    asyncio.run(PostgresStore(os.environ["DATABASE_URL"]).init_schema())
    print("tables ready")


def set_webhook(base_url: str) -> None:
    url = base_url.rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    print(tg("setWebhook", url=f"{url}/api/telegram", secret_token=os.environ["TELEGRAM_WEBHOOK_SECRET"],
             allowed_updates=["message", "edited_message"], drop_pending_updates=True))


def bot_profile() -> None:
    print(tg("setMyDescription", description=(
        "Ridely helps older adults and people with disabilities in the Princeton, NJ area find free or "
        "low-cost rides (for example to the doctor), and tells you exactly who to call. "
        "Ridely does not book rides. English y español.")))
    print(tg("setMyShortDescription", short_description="Find free or low-cost rides around Princeton, NJ."))
    print(tg("setMyCommands", commands=[
        {"command": "start", "description": "Start over and see what Ridely does"},
        {"command": "reset", "description": "Forget this chat and start a new trip"},
        {"command": "help", "description": "How to use Ridely"},
    ]))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init-db":
        init_db()
    elif cmd == "set-webhook":
        set_webhook(sys.argv[2])
    elif cmd == "webhook-info":
        print(tg("getWebhookInfo"))
    elif cmd == "bot-profile":
        bot_profile()
    else:
        print(__doc__)
