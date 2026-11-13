"""Build the chat model from environment variables. Any OpenAI-compatible provider works."""

from __future__ import annotations

import os

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider


def split_spec(spec: str) -> tuple[str, str | None]:
    """'zai-org/GLM-5.3@low' -> ('zai-org/GLM-5.3', 'low'). No '@' -> effort from LLM_REASONING_EFFORT."""
    name, _, effort = spec.partition("@")
    return name, (effort or os.environ.get("LLM_REASONING_EFFORT") or None)


def model_from_env(model_name: str | None = None) -> OpenAIChatModel:
    """Model name may end in '@<effort>' (none|minimal|low|medium|high) to set reasoning effort."""
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    spec = model_name or os.environ.get("LLM_MODEL")
    if not (base_url and api_key and spec):
        raise RuntimeError("Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL (see .env.example).")
    name, effort = split_spec(spec)
    settings = OpenAIChatModelSettings(openai_reasoning_effort=effort) if effort else None
    return OpenAIChatModel(name, provider=OpenAIProvider(base_url=base_url, api_key=api_key), settings=settings)
