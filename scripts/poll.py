"""Run the Telegram bot on this computer with long polling. No Vercel or webhook needed.

    uv run python scripts/poll.py

Needs TELEGRAM_BOT_TOKEN and the LLM_* settings in .env. DATABASE_URL is optional
(empty = chats are kept in memory until you stop the script).
Note: polling and a webhook cannot run at the same time. This script removes any webhook first.
Run `scripts/setup.py set-webhook ...` again after you stop it, to go back to Vercel.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from ridely.agent import current_mode  # noqa: E402
from ridely.bot import handle_update  # noqa: E402
from ridely.model import model_from_env  # noqa: E402
from ridely.store import get_store  # noqa: E402
from ridely.telegram import TelegramClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    tg = TelegramClient()
    model = model_from_env()
    store = get_store()
    me = await tg.call("getMe")
    print(f"Bot @{me['result']['username']} running. Model {os.environ.get('LLM_MODEL')}, mode {current_mode()}. Ctrl+C to stop.")
    await tg.call("deleteWebhook", drop_pending_updates=False)
    offset = 0
    while True:
        try:
            r = await tg.http.post(f"https://api.telegram.org/bot{tg.token}/getUpdates",
                                   json={"offset": offset, "timeout": 30}, timeout=40)
            updates = r.json().get("result", [])
        except Exception as e:  # network hiccup: wait and try again, do not stop the bot
            logging.warning("getUpdates failed (%s); retrying in 5 s", type(e).__name__)
            await asyncio.sleep(5)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            text = (upd.get("message") or {}).get("text", "")
            try:
                status = await handle_update(upd, store, tg, model)
            except Exception as e:
                status = f"crashed ({type(e).__name__})"
            logging.info("update %s %r -> %s", upd["update_id"], text[:60], status)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
