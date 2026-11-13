# Ridely

Ridely is a text agent. Older adults and people with disabilities in the Princeton, NJ area send it a message about a trip. Ridely tells them which free or low-cost ride program fits, the cost, who to call, what to say, and the last day to book. Ridely does not book rides.

See [PLAN.md](PLAN.md) for the design and [data/programs.md](data/programs.md) for the program rules.

## How it works

```
Telegram → Vercel (api/index.py) → ridely/bot.py → PydanticAI agent (ridely/agent.py)
                                                       ├─ tools: eligibility, service area, deadline,
                                                       │         destination lookup, geocoder, program facts
                                                       └─ any OpenAI-compatible model (LLM_* settings)
           Neon Postgres (ridely/store.py): chat history, seen updates, turn log
```

The code decides eligibility, service areas and deadlines (`ridely/tools/`). The model asks the questions and writes the replies.

`RIDELY_MODE` selects one of three versions:

| Mode | What it does |
|---|---|
| `minimal` | No tools. All program facts are in the prompt. The model decides everything. |
| `standard` (default) | 6 read-only tools. Code decides eligibility, area and deadlines. |
| `guardrails` | `standard` + reply checks (only phone numbers from our data; no program that the eligibility tool ruled out; short replies; no Markdown), a fixed 911 reply for emergencies, and removal of ID numbers before the model or the database sees them. |

## Setup (about 20 minutes)

You need: Python 3.12 and [uv](https://docs.astral.sh/uv/).

1. Install:
   ```
   uv sync
   cp .env.example .env
   ```
2. Fill in `.env`:
   - `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`: any OpenAI-compatible provider. The model must support tool calling.
   - `TELEGRAM_BOT_TOKEN`: in Telegram, message `@BotFather`, send `/newbot`, and copy the token.
   - `TELEGRAM_WEBHOOK_SECRET`: any long random string, e.g. `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`.
   - `DATABASE_URL` (optional for local runs): Neon connection string (use the **pooled** one).
3. Run the tests (no network, no cost):
   ```
   uv run pytest
   ```
4. Talk to Ridely in the terminal (uses the real model):
   ```
   uv run python scripts/chat.py
   ```
5. Run the Telegram bot from your computer (no deploy needed):
   ```
   uv run python scripts/setup.py bot-profile   # once: description and commands
   uv run python scripts/poll.py
   ```

## Deploy to Vercel

1. In Vercel, import the GitHub repo. Framework preset: **Other**. Vercel reads `requirements.txt`, `.python-version` and `vercel.json`.
2. Add the `.env` values as Environment Variables in the Vercel project.
3. If you use Neon, create the tables once: `uv run python scripts/setup.py init-db`.
4. After the deploy, point Telegram at it:
   ```
   uv run python scripts/setup.py set-webhook <your-app>.vercel.app
   uv run python scripts/setup.py webhook-info
   ```
5. Check `https://<your-app>.vercel.app/api/health`.

## Evals

`evals/scenarios.yaml` has 20 test conversations (English and Spanish, Medicaid, emergencies, prompt injection, edge cases). Run them on real models:

```
uv run python scripts/eval.py --models <model-a> <model-b> --modes minimal standard guardrails \
    --price-in <$/1M input> --price-out <$/1M output> --budget 2.00
```

Results (pass/fail and full transcripts) go to `evals/results/`.

## Change program facts

1. Edit `data/programs.md` (for people) and `data/programs.yaml` (for the code). Keep them the same.
2. Update `last_verified` and `sources`.
3. Run `uv run pytest`.

## Files

| Path | What |
|---|---|
| `api/index.py` | FastAPI app: `/api/telegram` webhook, `/api/health` |
| `ridely/agent.py` | Agent, tools, modes, history |
| `ridely/prompt.py` | Instructions for the model |
| `ridely/tools/` | Eligibility, service area, deadline, destination lookup |
| `ridely/geo.py` | Town table + U.S. Census geocoder |
| `ridely/guardrails.py` | Checks for `guardrails` mode |
| `ridely/bot.py` | One Telegram update: commands, agent turn, errors |
| `ridely/store.py` | Neon Postgres or in-memory storage |
| `data/` | Program facts and common destinations |
| `scripts/` | `chat.py`, `poll.py`, `setup.py`, `eval.py` |
