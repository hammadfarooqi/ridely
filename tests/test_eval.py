import asyncio
import importlib.util
from datetime import datetime
from pathlib import Path

import yaml
from pydantic_ai.models.test import TestModel

from ridely.agent import TZ
from ridely.data import known_phone_numbers

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("eval_script", ROOT / "scripts/eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

SCEN = yaml.safe_load((ROOT / "evals/scenarios.yaml").read_text())


def test_scenarios_are_well_formed():
    ids = [s["id"] for s in SCEN["scenarios"]]
    assert len(ids) == len(set(ids))
    known = known_phone_numbers()
    for s in SCEN["scenarios"]:
        assert s["turns"], s["id"]
        for p in s.get("expect_phones_any", []) + s.get("forbid_phones", []):
            assert ev.digits(p) in known, f"{s['id']}: {p} not in program data"


def test_grade():
    sc = {"expect_phones_any": ["609-530-1971"], "forbid_phones": ["866-527-9933"],
          "expect_text_any": ["today"], "forbid_text": ["1234"]}
    assert ev.grade(sc, ["Call TRADE at (609) 530-1971 today."]) == []
    fails = ev.grade(sc, ["Call 866.527.9933 tomorrow. 1234"])
    assert len(fails) == 4
    assert ev.grade({"expect_question": True, "expect_no_phone": True}, ["Where do you live?"]) == []
    assert ev.grade({"expect_no_phone": True}, ["call 609-452-5144"]) != []


def test_run_scenario_with_fake_model():
    now = datetime(2026, 11, 13, 10, tzinfo=TZ)
    sc = {"id": "x", "turns": ["a", "b"], "expect_question": True}
    res = asyncio.run(ev.run_scenario(sc, TestModel(call_tools=[], custom_output_text="Where to?"), now))
    assert res["pass"] and len(res["replies"]) == 2 and res["requests"] == 2
