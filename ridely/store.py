"""Conversation storage. Postgres (Neon) in production; in-memory for local runs and tests.

The prototype keeps all data. Add a retention rule before any pilot (see PLAN.md).
"""

from __future__ import annotations

import os
from typing import Protocol

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    chat_id     BIGINT PRIMARY KEY,
    messages    JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS seen_updates (
    update_id   BIGINT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS turns (
    id            BIGSERIAL PRIMARY KEY,
    chat_id       BIGINT NOT NULL,
    model         TEXT,
    requests      INT,
    input_tokens  INT,
    output_tokens INT,
    latency_ms    INT,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class Store(Protocol):
    async def first_time_seen(self, update_id: int) -> bool: ...
    async def get_history(self, chat_id: int) -> bytes | None: ...
    async def save_history(self, chat_id: int, history_json: bytes) -> None: ...
    async def reset(self, chat_id: int) -> None: ...
    async def log_turn(self, chat_id: int, **fields) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self.seen: set[int] = set()
        self.history: dict[int, bytes] = {}
        self.turns: list[dict] = []

    async def first_time_seen(self, update_id: int) -> bool:
        if update_id in self.seen:
            return False
        self.seen.add(update_id)
        return True

    async def get_history(self, chat_id: int) -> bytes | None:
        return self.history.get(chat_id)

    async def save_history(self, chat_id: int, history_json: bytes) -> None:
        self.history[chat_id] = history_json

    async def reset(self, chat_id: int) -> None:
        self.history.pop(chat_id, None)

    async def log_turn(self, chat_id: int, **fields) -> None:
        self.turns.append({"chat_id": chat_id, **fields})


class PostgresStore:
    """One short connection per call. Works with Neon's pooled connection string on serverless."""

    def __init__(self, url: str) -> None:
        self.url = url

    async def _conn(self):
        import psycopg

        return await psycopg.AsyncConnection.connect(self.url, autocommit=True, prepare_threshold=None)

    async def init_schema(self) -> None:
        async with await self._conn() as c:
            await c.execute(SCHEMA)

    async def first_time_seen(self, update_id: int) -> bool:
        async with await self._conn() as c:
            cur = await c.execute(
                "INSERT INTO seen_updates (update_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING update_id",
                (update_id,),
            )
            return (await cur.fetchone()) is not None

    async def get_history(self, chat_id: int) -> bytes | None:
        async with await self._conn() as c:
            cur = await c.execute("SELECT messages::text FROM conversations WHERE chat_id = %s", (chat_id,))
            row = await cur.fetchone()
            return row[0].encode() if row else None

    async def save_history(self, chat_id: int, history_json: bytes) -> None:
        async with await self._conn() as c:
            await c.execute(
                "INSERT INTO conversations (chat_id, messages, updated_at) VALUES (%s, %s::jsonb, now()) "
                "ON CONFLICT (chat_id) DO UPDATE SET messages = EXCLUDED.messages, updated_at = now()",
                (chat_id, history_json.decode()),
            )

    async def reset(self, chat_id: int) -> None:
        async with await self._conn() as c:
            await c.execute("DELETE FROM conversations WHERE chat_id = %s", (chat_id,))

    async def log_turn(self, chat_id: int, **f) -> None:
        async with await self._conn() as c:
            await c.execute(
                "INSERT INTO turns (chat_id, model, requests, input_tokens, output_tokens, latency_ms, error) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (chat_id, f.get("model"), f.get("requests"), f.get("input_tokens"), f.get("output_tokens"),
                 f.get("latency_ms"), f.get("error")),
            )


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        url = os.environ.get("DATABASE_URL")
        _store = PostgresStore(url) if url else MemoryStore()
    return _store
