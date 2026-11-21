import asyncio
import importlib.util
from datetime import datetime
from pathlib import Path

from pydantic_ai.models.test import TestModel

from ridely.agent import TZ
from ridely.data import known_phone_numbers

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("eval_script", ROOT / "scripts/eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

CHECK_KEYS = {"expect_phones_any", "expect_phones_all", "forbid_phones", "expect_deadline_any", "expect_text_any",
              "forbid_text", "expect_question", "expect_no_phone"}
CATEGORIES = {"rules", "safety", "privacy", "injection", "conversation", "language", "scope", "robustness"}


def test_scenarios_are_well_formed():
    scen = ev.load_scenarios()
    assert len(scen) >= 150
    ids = [s["id"] for s in scen]
    assert len(ids) == len(set(ids))
    known = known_phone_numbers()
    for s in scen:
        assert s["turns"], s["id"]
        assert s["category"] in CATEGORIES, s["id"]
        assert CHECK_KEYS & set(s), f"{s['id']} has no checks"
        assert not set(k for k in s if k.startswith(("expect", "forbid"))) - CHECK_KEYS, s["id"]
        for p in s.get("expect_phones_any", []) + s.get("expect_phones_all", []):
            assert ev.digits(p) in known, f"{s['id']}: {p} not in program data"


def test_generated_scenarios_are_up_to_date_and_oracle_agrees_with_code():
    import sys
    sys.path.insert(0, str(ROOT))
    from evals.build_scenarios import build, render

    scenarios, disagree = build()
    assert disagree == [], f"oracle and ridely code disagree: {disagree}"
    assert (ROOT / "evals/generated.yaml").read_text() == render(scenarios), \
        "evals/generated.yaml is stale: run `uv run python evals/build_scenarios.py`"


def test_grade():
    sc = {"expect_phones_any": ["609-530-1971"], "forbid_phones": ["866-527-9933"],
          "expect_text_any": ["today"], "forbid_text": ["1234"]}
    assert ev.grade(sc, ["Call TRADE at (609) 530-1971 today."]) == []
    fails = ev.grade(sc, ["Call 1-866.527.9933 tomorrow. 1234"])
    assert len(fails) == 4
    assert ev.grade({"expect_phones_any": ["866-527-9933"]}, ["Call 1-866-527-9933."]) == []
    assert ev.grade({"expect_deadline_any": ["November 18"]}, ["Call by Wednesday, November 18."]) == []
    assert ev.format_issues(["**Hi**", "a? b? c?", "fine"]) == ["format: Markdown in reply 1", "format: 3 questions in reply 2"]
    assert ev.grade({"expect_question": True, "expect_no_phone": True}, ["Where do you live?"]) == []
    assert ev.grade({"expect_no_phone": True}, ["call 609-452-5144"]) != []


def test_run_scenario_with_fake_model():
    now = datetime(2026, 11, 13, 10, tzinfo=TZ)
    sc = {"id": "x", "turns": ["a", "b"], "expect_question": True}
    res = asyncio.run(ev.run_scenario(sc, TestModel(call_tools=[], custom_output_text="Where to?"), now))
    assert res["pass"] and len(res["replies"]) == 2 and res["requests"] == 2
