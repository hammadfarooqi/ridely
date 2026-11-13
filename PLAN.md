# Ridely — Build Plan

Last updated: 2026-11-13

Ridely is a text agent. Older adults and residents with disabilities in Princeton text Ridely about a trip. Ridely tells them which free or low-cost ride program fits, what it costs, who to call, what to say, and the last day to book. Ridely is read-only. It does not book rides. It does not ask for names or IDs.

The prototype runs on Telegram. A later pilot can move to SMS (for example, Twilio) with the same agent code.

---

## 0. Decisions (2026-11-13)

| Topic | Decision |
|---|---|
| Language | Python 3.12 (FastAPI app in `api/index.py` on Vercel's Python runtime). Local tool: `uv`. |
| Agent framework | **PydanticAI** (`pydantic-ai-slim[openai]`). Reasons: tool inputs checked by Pydantic with automatic retries, `TestModel` for free tests, JSON history adapter, `RunContext` for tool dependencies, `UsageLimits` to stop endless tool loops, small (46 MB, no AWS packages). We compared it with Strands and with writing our own loop. |
| LLM provider | Use our credits: Prime Intellect inference and Sail. Both give an OpenAI-compatible chat API, so the first adapter uses that format (`openai` Python client with a custom `base_url`). We can add an Anthropic adapter later. **Open:** choose the model (see §4c). |
| Rules data | Team edits `data/programs.md` in git. A script converts it to `data/programs.yaml`. |
| Conversation store | Postgres on Neon. The prototype keeps all data (no automatic delete). We add a delete rule before any pilot with real residents. |

## 1. Architecture

```
Resident ──text──▶ Telegram ──webhook──▶ Vercel function
                                             │
                               load history (Neon Postgres, by chat_id)
                                             │
                                       Agent harness  ◀──▶  LLM provider (adapter)
                                             │
                                     read-only tools
                                             │
                            reply via Telegram sendMessage
```

Main parts:

| Part | What it does |
|---|---|
| `api/telegram` webhook | Checks the secret header. Ignores repeat `update_id`s. Sends a "typing" action. Runs the agent. Sends the reply. |
| Agent | A PydanticAI `Agent` with 6 tools. `UsageLimits(request_limit=6)` stops long tool loops. `RunContext` gives tools the database connection and today's date. |
| Model connection | `OpenAIChatModel` + `OpenAIProvider(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)`. To change provider, change the env vars only. |
| Conversation store | Neon table `conversations(chat_id, messages JSONB, updated_at)` (history saved with `ModelMessagesTypeAdapter`) + table `seen_updates(update_id)`. No names or IDs. |

File layout:

```
api/index.py         FastAPI webhook (Telegram → agent → reply)
ridely/agent.py      PydanticAI Agent, system prompt, tool registration
ridely/model.py      OpenAI-compatible model setup from env vars
ridely/tools/*.py    6 tools (plain functions with typed inputs)
ridely/store.py      Neon read/write (psycopg, plain SQL)
ridely/telegram.py   sendMessage / sendChatAction with httpx
data/programs.yaml
tests/               unit tests + scenario tests with TestModel / FunctionModel
```
| Programs data | `data/programs.yaml` — made from the master rules document. Each entry has a `last_verified` date. |

## 2. System prompt (stable, so we can cache it)

1. Ridely's role and rules: plain language, short messages, English or Spanish (reply in the user's language), no booking, no personal data, always say "rules can change — confirm when you call", give 911 for emergencies.
2. A short summary of each program (2–3 lines each).
3. The conversation flow: find out (a) the age or disability, (b) the start town, (c) the destination, (d) the trip date and time, (e) whether the user has Medicaid. Ask only for the facts that are still missing. Ask one or two questions in each message.

Put today's date and day of week in a message **after** the stable prompt. If we put it inside the stable prompt, the prompt cache breaks every day.

## 3. Tools (all read-only)

| Tool | Input | Output | How |
|---|---|---|---|
| `get_program_details` | `program_id` | Full fact sheet + `last_verified` date | Read from `programs.yaml` |
| `check_eligibility` | age, disability, Medicaid, home town | Programs that can fit, and why | Deterministic rules in code. The model does not decide eligibility alone. |
| `geocode_address` | address or town | Lat/lon, town (county subdivision), county | U.S. Census Geocoder, `geographies/onelineaddress`. No API key needed. |
| `check_service_area` | `program_id`, start, end | in / out / unsure, with reason | Compare town and county against the program's area list |
| `lookup_destination` | name, e.g. "RWJ Hamilton" | Address + town | Our own list of ~40 common places (hospitals, clinics, ShopRite, senior centers). Fuzzy match. If no match, the model asks for the street address. |
| `booking_deadline` | `program_id`, trip date | Last day and time to call | Business-day math with a holiday list |

Note: the Census geocoder works on street addresses only. It cannot find "RWJ Hamilton". That is why we need `lookup_destination`.

## 4. The three non-code workstreams

### 4a. Master rules document (team)

- Starting draft: [data/programs.md](data/programs.md). It covers Crosstown, RideProvide, Mercer TRADE, Access Link, Medicaid NEMT, and Princeton Muni Transit. Many values are marked **VERIFY**, because the sources do not agree.
- Team task: call each program, or read its current website. Fill in each VERIFY field. Write the date and the source for each field.
- Then convert the document to `data/programs.yaml` (one schema for all programs: `id, name, operator, eligibility, service_area, cost, booking {phone, hours, min_notice, max_advance}, trip_purposes, how_to_register, what_to_say, last_verified, sources`).
- Each team member owns 2 programs.

### 4b. Telegram bot

1. Open Telegram. Message `@BotFather`. Send `/newbot`. Name it "Ridely". Save the token.
2. Use `/setdescription` and `/setcommands` (`/start`, `/reset`, `/espanol`).
3. Deploy the webhook to Vercel. Then call `setWebhook` with the URL and a `secret_token`.
4. In the handler, check the `X-Telegram-Bot-Api-Secret-Token` header. Reject requests with a wrong value.
5. Telegram sends an update again if it does not get a 200 response. Store each `update_id` you handle, and skip repeats.

### 4c. Inference provider

1. Get API keys from Prime Intellect and Sail. Find each one's OpenAI-compatible `base_url`.
2. List the models each provider serves. Keep only models that support **tool calling** (function calling) on that endpoint. Not all hosted open models do.
3. Pick 2–3 candidate models. Run the M5 test set on each. Choose the one with the best accuracy on eligibility and the fewest bad tool calls. Write the result in this file.
4. Put settings in Vercel environment variables: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `DATABASE_URL`. To change the provider, change only these values.
5. Set a spend alert if the provider has one.

Estimated use: a conversation is about 5 turns × ~4k input tokens, so ~20k tokens. The credits should cover the whole project.

## 5. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Setup | Repo, Vercel project, bot token, API key, Neon database all work |
| M1 | Echo bot | Telegram → Vercel → reply works |
| M2 | Agent loop, no tools | Ridely chats from the system prompt only |
| M3 | Tools | All 6 tools work and have unit tests |
| M4 | Verified data | Team finishes the VERIFY fields; `programs.yaml` is complete |
| M5 | Test set | (a) Free tests with `TestModel`/`FunctionModel` for tool wiring and history. (b) 20–30 scripted scenarios (English and Spanish, edge cases) run on the real model, with the expected program. We run them before each change and use them to choose the model. |
| M6 | Demo | Live demo + a short report on accuracy over the test set |
| M7 (later) | Pilot | Show to Center for Modern Aging and Human Services staff; plan the SMS number |

## 6. Safety and privacy rules

- Never book. Never ask for names, dates of birth, Medicaid ID numbers, or full home addresses unless the user wants a service-area check. A cross street or town is enough.
- Always show the date the program data was last checked.
- If Ridely is not sure, it says so and gives a person to call (for example, the CMAP number or NJ 211).
- Emergencies: tell the user to call 911.
- Log only what we need to debug. The prototype does not delete conversations. Before a pilot (M7), add a retention rule (for example, delete after 7 days).

## 7. Risks

| Risk | What we do |
|---|---|
| Program rules change or sources disagree | `last_verified` dates; the team calls each program; Ridely tells users to confirm |
| The model says a person is eligible when they are not | Eligibility is in code (`check_eligibility`), not only in the prompt |
| Open model makes bad tool calls | PydanticAI checks each tool input and sends errors back to the model (set `retries` per tool; raise `ModelRetry` with a hint). Pick the model with the test set (M5). |
| Credits run out | The adapter uses the OpenAI-compatible format, so we can move to another provider by changing env vars. |
| Telegram is not what seniors use | Prototype only. The harness does not depend on the channel, so SMS can replace it. |
| Vercel function time limit | Set `maxDuration`. Limit tool rounds. Send "typing" while the agent works. |
