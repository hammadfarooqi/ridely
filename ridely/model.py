"""Build the chat model from environment variables. Any OpenAI-compatible provider works."""

from __future__ import annotations

import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


def model_from_env(model_name: str | None = None) -> OpenAIChatModel:
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    name = model_name or os.environ.get("LLM_MODEL")
    if not (base_url and api_key and name):
        raise RuntimeError("Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL (see .env.example).")
    return OpenAIChatModel(name, provider=OpenAIProvider(base_url=base_url, api_key=api_key))
