# Morning recap — night of 2026-11-13

## Summary

- **The code is complete and tested offline.** This includes the PydanticAI agent with 6 tools, the Telegram webhook for Vercel, Postgres storage, a local polling bot, 20 test scenarios with a runner that has a cost limit, and 3 modes (minimal / standard / guardrails). 61 tests pass, and the code is pushed to the private repo `hammadfarooqi/ridely`.
- **Ridely has not talked to a real model yet.** The Prime Intellect key in `cos597c/project1/code/tau2-bench/.env` is rejected ("Invalid or expired token, or user not part of team"). I did not find a Sail key.
- **I could not create any accounts.** Telegram Web, Prime Intellect, Neon and Vercel were all logged out in Chrome. Telegram needs your phone, and Prime Intellect has a CAPTCHA. I stopped at each login page.

## What you need to do (about 20 minutes)

1. **Model key.** Get a new Prime Intellect API key (or a Sail key) and its base URL. In `.env`, set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`. Choose a model that supports tool calling.
2. **Telegram bot.** In Telegram, message `@BotFather` and send `/newbot` (suggested name "Ridely", username like `RidelyPrincetonBot`). Put the token in `.env` as `TELEGRAM_BOT_TOKEN`. Then run `uv run python scripts/setup.py bot-profile`.
3. **Try it right away:** `uv run python scripts/poll.py`, then text your bot. You need no deploy and no database for this.
4. **Choose the model (budget $2):**
   ```
   uv run python scripts/eval.py --models <model-a> <model-b> --modes standard guardrails minimal \
       --price-in <$/1M in> --price-out <$/1M out> --budget 2.00
   ```
   My estimate is about $0.10–0.30 for each model and mode at typical open-model prices. Read the transcripts in `evals/results/*.md`.
5. **Neon:** create a project and copy the **pooled** connection string to `DATABASE_URL`. Then run `uv run python scripts/setup.py init-db`.
6. **Vercel:** import the GitHub repo (preset "Other") and add the env vars. Then run `uv run python scripts/setup.py set-webhook <app>.vercel.app`.
7. **Team:** call Crosstown (CMAP), TRADE, ModivCare and Princeton (Muni bus) to fill in the remaining **VERIFY** fields in `data/programs.md` and `data/programs.yaml`.

## What I did (commits on `main`)

| Commit | What |
|---|---|
| `a80c0fe` | Agent, 6 tools, webhook, store, Telegram client, 42 tests |
| `07b561d` | 20 scenarios, `scripts/eval.py`, safe webhook fallback |
| `0e3d5c6` | RideProvide and Access Link facts from their official pages |
| `5a2a217` | `RIDELY_MODE`: minimal / standard / guardrails |
| `b3c4e39` | Local polling bot, README setup guide, plan status |
| `ec0addb` | Bug fix: town names ("Princeton", "Plainsboro") matched hospitals |

Data findings:
- **RideProvide trips to RWJ Hamilton and to Penn Medicine Princeton Health are free.** This matches the example in your proposal. RideProvide fares are $9 / $13.50 / $19 / $22.50, based on zones. Rides must be booked 2 business days ahead by phone.
- **Access Link takes reservations every day,** 7:30 am to 4 pm for the next day. It has no same-day trips. The service area is within ¾ mile of local bus routes.
- **Princeton Fitness & Wellness is in Montgomery (Somerset County),** not in Princeton, even though its address says "Princeton".
- **The Mercer County TRADE pages block automated reading (HTTP 403).** Read them in a browser or call TRADE.

## Decisions I made (all easy to change)

| # | Decision | Why | To change |
|---|---|---|---|
| 1 | I built the "minimal" and "guardrails" versions as **modes** (`RIDELY_MODE`), not git branches. | One codebase, and one eval run compares all three. | Set `RIDELY_MODE` in `.env` or Vercel. |
| 2 | **Guardrails mode** does these things. Phone numbers in a reply must come from our data, plus 911 and 211. A reply must not recommend a program that the eligibility tool ruled out. Replies are at most 1400 characters, and Markdown is removed. Clear emergencies get a fixed 911 reply (and 988), without the model. ID-like numbers (9 digits, or 11 or more) are removed before the model or the database sees them. | These are the main risks for elderly users with open models. | `ridely/guardrails.py` |
| 3 | **Standard mode is the default.** | It is the design from the proposal. | `.env.example` |
| 4 | Booking notice: Crosstown 3 business days (a guess), TRADE 7 days (at most 14 ahead), RideProvide 2 business days, Medicaid 2 business days, Access Link 1 day by 4 pm (every day). Weekends and federal holidays do not count as business days. | Web sources; Crosstown is not confirmed. | `data/programs.yaml` → `booking.notice` |
| 5 | Crosstown age rule = **65+, or visually impaired** (taken from RideProvide, its provider). No source states it. | The team must check this by phone. | `programs.yaml` → `crosstown.eligibility` |
| 6 | A list of 21 common destinations; 17 of the addresses were checked with the Census geocoder. Bare town names use a built-in town table (Mercer County + neighboring towns). | The Census geocoder cannot find place names or town names. | `data/destinations.yaml`, `ridely/geo.py` |
| 7 | The model sees the last 12 user turns. Each turn makes at most 8 model calls. Each tool gets 2 retries. | Limits cost and stops tool loops. | `ridely/agent.py` constants |
| 8 | Replies are plain text with no Markdown. `/start` and `/reset` clear the history. | Telegram shows raw `**` symbols when Markdown is not turned on. | `ridely/prompt.py`, `ridely/bot.py` |
| 9 | When something fails, the user gets the CMAP number (609-751-9699) and 2-1-1. The webhook always returns 200, even when the model settings are missing. | Otherwise Telegram resends the message forever, and the user gets no reply. | `ridely/bot.py`, `api/index.py` |
| 10 | Database tables: `conversations` (JSONB history), `seen_updates`, and `turns` (tokens, time, errors; no message text). Nothing is deleted, as you asked. | Tracks cost and speed without copying the messages. | `ridely/store.py` |
| 11 | Repo `ridely` is private. I removed your email address from `pyproject.toml`. | Privacy. | GitHub settings |
| 12 | Vercel: `api/index.py`, Python 3.12, `maxDuration` 60 s, and a `requirements.txt` generated from `uv.lock` (62 MB installed). | Standard Vercel Python setup. | `vercel.json` |

## Things I need your OK on

1. **Vault file:** I overwrote `obsidian_vault/research/ridely-ride-programs/princeton-ride-programs.md` with the updated facts. I created that file earlier tonight, but your rule says to ask before changing an existing vault file. Sorry; I will not change it again without asking.
2. **Local model (free testing):** a small open model such as Qwen3 4B (about 2.5 GB, from Hugging Face) with the `llama-server` you already built could run the evals for free. I did not download it, because downloads need your OK.
3. **Existing keys I found but did not use:** `recruit_easy/backend/.env` has an Anthropic API key and a Telegram bot token. Using them would charge a different account and take over that bot, so I left them alone. Tell me if you want me to use either one.
4. **"Sail":** I could not find what this provider is, or a key for it. Send me the base URL and where the key is stored.

## Known limits

- **No real-model results yet.** Prompt quality and tool use are untested on a real model.
- **Access Link service area:** Ridely cannot check distance to a bus route, so it always says "unsure, ask Access Link".
- **Two quick messages in the same chat can overwrite each other's history.** The second save wins. This is fine for a prototype.
- **Each database call opens a new connection** (about 5 for each message). This works with Neon's pooled connection string, but it adds some delay.
