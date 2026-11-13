"""Load program and destination data from data/*.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache
def programs() -> dict[str, dict[str, Any]]:
    """All programs, keyed by id."""
    items = yaml.safe_load((DATA_DIR / "programs.yaml").read_text())
    return {p["id"]: p for p in items}


@lru_cache
def destinations() -> list[dict[str, Any]]:
    return yaml.safe_load((DATA_DIR / "destinations.yaml").read_text())


def program_summaries() -> str:
    """Short text list of programs for the system prompt."""
    lines = []
    for p in programs().values():
        lines.append(f"- {p['name']} (id: {p['id']}): {p['summary']}")
    return "\n".join(lines)


def known_phone_numbers() -> set[str]:
    """Every phone number that appears in the program data (digits only)."""
    import re

    nums: set[str] = set()
    for p in programs().values():
        text = yaml.safe_dump(p)
        for m in re.findall(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text):
            nums.add(re.sub(r"\D", "", m))
    return nums
