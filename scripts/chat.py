"""Chat with Ridely in the terminal, using the real model from .env. No Telegram, no database.

    uv run python scripts/chat.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from ridely.agent import run_turn  # noqa: E402
from ridely.model import model_from_env  # noqa: E402


async def main() -> None:
    model = model_from_env()
    history = None
    print("Ridely (type 'quit' to stop, 'reset' to start over)")
    while True:
        try:
            text = input("\nyou> ").strip()
        except EOFError:
            break
        if text in ("quit", "exit"):
            break
        if text == "reset":
            history = None
            continue
        r = await run_turn(text, history, model)
        history = r.history_json
        print(f"\nridely> {r.reply}\n  [{r.requests} model calls, {r.input_tokens} in / {r.output_tokens} out tokens]")


if __name__ == "__main__":
    asyncio.run(main())
