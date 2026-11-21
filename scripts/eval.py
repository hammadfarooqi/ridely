"""Run the Ridely test suite on real models.

    uv run python scripts/eval.py --models deepseek-ai/DeepSeek-V4.1-Flash-1017 --modes standard
    uv run python scripts/eval.py --models <id> --split dev          # only the dev set (not holdout)
    uv run python scripts/eval.py --models <id> --category safety injection
    uv run python scripts/eval.py --models <id> --only gen_age_60_trade emergency

Scenarios: evals/scenarios.yaml (hand-written behavior tests) + evals/generated.yaml (rule coverage, built by
evals/build_scenarios.py with expected answers from evals/oracle.py). See evals/README.md for the method.

Each scenario is scored on separate checks:
  right_program  the reply gives a phone number of a program that fits   (expect_phones_any / _all)
  wrong_program  the reply does not give a number of a program that does not fit  (forbid_phones)
  deadline       the reply says the last day to call                     (expect_deadline_any)
  behavior       required/forbidden text, asks a question, no phone       (expect_text_any, forbid_text, ...)
  format         soft checks on every reply: no Markdown, at most 2 questions, at most 1400 characters
A scenario PASSES when every check except format passes. Format is reported on its own.
Results (JSON + readable transcripts) go to evals/results/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from ridely.agent import TZ, Deps, run_turn  # noqa: E402
from ridely.model import model_from_env, split_spec  # noqa: E402

PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
SCENARIO_FILES = [ROOT / "evals/scenarios.yaml", ROOT / "evals/generated.yaml"]
TODAY = "2026-11-13T10:00"
HARD = ("right_program", "wrong_program", "deadline", "behavior", "error")


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)[-10:]


def phones_in(text: str) -> set[str]:
    return {digits(m) for m in PHONE_RE.findall(text)}


def load_scenarios() -> list[dict]:
    out = []
    for f in SCENARIO_FILES:
        if f.exists():
            for s in yaml.safe_load(f.read_text())["scenarios"]:
                s.setdefault("category", "rules")
                out.append(s)
    return out


def grade(sc: dict, replies: list[str]) -> list[str]:
    """List of failed checks, each 'check_type: detail'. Empty = all checks pass."""
    final = replies[-1] if replies else ""
    low = final.lower()
    found = phones_in(final)
    fails = []
    if sc.get("expect_phones_any") and not any(digits(p) in found for p in sc["expect_phones_any"]):
        fails.append(f"right_program: none of phones {sc['expect_phones_any']}")
    for p in sc.get("expect_phones_all", []):
        if digits(p) not in found:
            fails.append(f"right_program: missing phone {p}")
    bad = [p for p in sc.get("forbid_phones", []) if digits(p) in found]
    if bad:
        fails.append(f"wrong_program: gave {bad}")
    if sc.get("expect_deadline_any") and not any(t.lower() in low for t in sc["expect_deadline_any"]):
        fails.append(f"deadline: none of {sc['expect_deadline_any'][:5]}")
    if sc.get("expect_text_any") and not any(t.lower() in low for t in sc["expect_text_any"]):
        fails.append(f"behavior: none of text {sc['expect_text_any'][:6]}")
    all_text = " ".join(replies).lower()
    for t in sc.get("forbid_text", []):
        if t.lower() in all_text:
            fails.append(f"behavior: forbidden text {t!r}")
    if sc.get("expect_question") and not ("?" in final or "¿" in final):
        fails.append("behavior: expected a question")
    if sc.get("expect_no_phone") and found:
        fails.append(f"behavior: expected no phone, found {sorted(found)}")
    return fails


def format_issues(replies: list[str]) -> list[str]:
    issues = []
    for i, r in enumerate(replies, 1):
        if "**" in r or re.search(r"^#{1,6}\s", r, re.M) or "|---" in r:
            issues.append(f"format: Markdown in reply {i}")
        if r.count("?") > 2:
            issues.append(f"format: {r.count('?')} questions in reply {i}")
        if len(r) > 1400:
            issues.append(f"format: {len(r)} characters in reply {i}")
    return issues


# Sail ASAP prices, $ per 1M tokens (input, output), from docs.sailresearch.com/pricing (2026-11-13).
PRICES = {
    "moonshotai/Kimi-K3": (1.50, 10.00), "moonshotai/Kimi-K3.1-Pro": (1.50, 10.00),
    "zai-org/GLM-5.3": (0.98, 3.08), "zai-org/GLM-5.3-Flash": (0.11, 0.35),
    "deepseek-ai/DeepSeek-V4-Flash-0731": (0.09, 0.18), "deepseek-ai/DeepSeek-V4.1-Flash-1017": (0.09, 0.30),
    "deepseek-ai/DeepSeek-V4-Pro-0813": (0.92, 2.77), "google/gemma-4-31B-it": (0.40, 0.60),
    "google/gemma-4-12B-it": (0.30, 2.00), "nvidia/Gemma-4-31B-IT-NVFP4": (0.14, 0.40),
}


async def run_scenario(sc: dict, model, now: datetime, mode: str = "standard") -> dict:
    history = None
    replies, req, tin, tout, turn_secs = [], 0, 0, 0, []
    t0 = time.monotonic()
    error = None
    try:
        for turn in sc["turns"]:
            t1 = time.monotonic()
            r = await run_turn(turn, history, model, Deps(now=now), mode=mode)
            turn_secs.append(round(time.monotonic() - t1, 1))
            history = r.history_json
            replies.append(r.reply)
            req += r.requests
            tin += r.input_tokens
            tout += r.output_tokens
    except Exception as e:  # count as a failure, keep going
        error = f"{type(e).__name__}: {e}"[:400]
    fails = [f"error: {error}"] if error else grade(sc, replies)
    fmt = [] if error else format_issues(replies)
    return {"id": sc["id"], "category": sc.get("category", "rules"), "holdout": bool(sc.get("holdout")),
            "pass": not fails, "fails": fails, "format": fmt, "note": sc.get("note"),
            "turns": sc["turns"], "replies": replies, "requests": req, "input_tokens": tin, "output_tokens": tout,
            "seconds": round(time.monotonic() - t0, 1), "turn_seconds": turn_secs}


def report(label: str, results: list[dict]) -> list[str]:
    n = len(results)
    lines = [f"# Eval: {label}", "", f"Passed {sum(r['pass'] for r in results)}/{n}. "
             f"Format clean {sum(not r['format'] for r in results)}/{n}. "
             f"Est. cost ${sum(r['cost'] for r in results):.4f}.", ""]
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    lines += ["| Category | Passed |", "|---|---|"]
    lines += [f"| {c} | {sum(x['pass'] for x in rs)}/{len(rs)} |" for c, rs in sorted(by_cat.items())]
    checks = Counter(f.split(":")[0] for r in results for f in r["fails"] + r["format"])
    lines += ["", "| Failed check | Count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in checks.most_common()]
    for split in ("dev", "holdout"):
        rs = [r for r in results if r["holdout"] == (split == "holdout")]
        if rs:
            lines.append(f"\n{split}: {sum(x['pass'] for x in rs)}/{len(rs)} passed")
    lines.append("")
    for r in sorted(results, key=lambda r: (r["pass"], r["id"])):
        lines.append(f"## {r['id']} ({r['category']}{', holdout' if r['holdout'] else ''}) — {'PASS' if r['pass'] else 'FAIL'}")
        if r["note"]:
            lines.append(f"Expected: {r['note']}")
        if r["fails"] or r["format"]:
            lines.append(f"Failed: {'; '.join(r['fails'] + r['format'])}")
        for u, a in zip(r["turns"], r["replies"]):
            lines += ["", f"> **User:** {u}", "", "```", a, "```"]
        lines.append("")
    return lines


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--modes", nargs="+", default=["standard"], choices=["minimal", "standard", "guardrails"])
    ap.add_argument("--only", nargs="*", help="scenario ids to run")
    ap.add_argument("--category", nargs="*", help="only these categories (rules, safety, privacy, ...)")
    ap.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--price-in", type=float, help="$ per 1M input tokens (default: PRICES table)")
    ap.add_argument("--price-out", type=float, help="$ per 1M output tokens (default: PRICES table)")
    ap.add_argument("--budget", type=float, default=2.0, help="stop starting new scenarios at this estimated $")
    args = ap.parse_args()

    now = datetime.fromisoformat(TODAY).replace(tzinfo=TZ)
    scenarios = [s for s in load_scenarios()
                 if (not args.only or s["id"] in args.only)
                 and (not args.category or s["category"] in args.category)
                 and (args.split == "all" or bool(s.get("holdout")) == (args.split == "holdout"))]
    out_dir = ROOT / "evals/results"
    out_dir.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    summary = []
    for name in args.models:
        model = model_from_env(name)
        base = split_spec(name)[0]
        p_in, p_out = PRICES.get(base, (None, None))
        p_in = args.price_in if args.price_in is not None else p_in
        p_out = args.price_out if args.price_out is not None else p_out
        if p_in is None or p_out is None:
            raise SystemExit(f"No price for {base}: pass --price-in and --price-out")
        for mode in args.modes:
            label = f"{name} [{mode}]"
            sem = asyncio.Semaphore(args.concurrency)
            results = []

            async def one(sc):
                nonlocal spent
                async with sem:
                    if spent >= args.budget:
                        return
                    res = await run_scenario(sc, model, now, mode)
                res["cost"] = round(res["input_tokens"] / 1e6 * p_in + res["output_tokens"] / 1e6 * p_out, 5)
                spent += res["cost"]
                results.append(res)
                print(f"[{label}] {sc['id']:<40} {'PASS' if res['pass'] else 'FAIL'}  ${res['cost']:.4f}  "
                      f"{res['seconds']}s  {'; '.join(res['fails'])}", flush=True)

            await asyncio.gather(*(one(sc) for sc in scenarios))
            if spent >= args.budget:
                print(f"budget reached (${spent:.3f}); some scenarios did not run")
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{name}-{mode}")
            (out_dir / f"{stamp}-{safe}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
            (out_dir / f"{stamp}-{safe}.md").write_text("\n".join(report(label, results)))
            secs = sorted(t for r in results for t in r.get("turn_seconds", []))
            med, mx = (secs[len(secs) // 2], secs[-1]) if secs else (0, 0)
            n_pass = sum(r["pass"] for r in results)
            summary.append((label, n_pass, len(results), sum(r["cost"] for r in results), med, mx))
            print(f"== {label}: {n_pass}/{len(results)} passed. Report: evals/results/{stamp}-{safe}.md")
    print("\nSummary:")
    for label, n, total, cost, med, mx in summary:
        print(f"  {label:<50} {n}/{total}  ${cost:.4f}  reply median {med}s, max {mx}s")


if __name__ == "__main__":
    asyncio.run(main())
