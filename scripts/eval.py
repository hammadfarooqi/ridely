"""Run the scenario tests (evals/scenarios.yaml) on one or more real models.

    uv run python scripts/eval.py --models <model-id> [<model-id> ...] \
        --modes minimal standard guardrails --price-in 0.10 --price-out 0.40 --budget 2.00

Prices are dollars per 1M tokens (look them up for your provider). The run stops when the
estimated cost reaches --budget. Results go to evals/results/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
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


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def phones_in(text: str) -> set[str]:
    return {digits(m) for m in PHONE_RE.findall(text)}


def grade(sc: dict, replies: list[str]) -> list[str]:
    """Returns a list of failed checks (empty = pass)."""
    final = replies[-1] if replies else ""
    low = final.lower()
    found = phones_in(final)
    fails = []
    if sc.get("expect_phones_any") and not any(digits(p) in found for p in sc["expect_phones_any"]):
        fails.append(f"none of phones {sc['expect_phones_any']}")
    for p in sc.get("expect_phones_all", []):
        if digits(p) not in found:
            fails.append(f"missing phone {p}")
    for p in sc.get("forbid_phones", []):
        if digits(p) in found:
            fails.append(f"forbidden phone {p}")
    if sc.get("expect_text_any") and not any(t.lower() in low for t in sc["expect_text_any"]):
        fails.append(f"none of text {sc['expect_text_any']}")
    for t in sc.get("forbid_text", []):
        if t.lower() in " ".join(replies).lower():
            fails.append(f"forbidden text {t!r}")
    if sc.get("expect_question") and not ("?" in final or "¿" in final):
        fails.append("expected a question")
    if sc.get("expect_no_phone") and found:
        fails.append(f"expected no phone, found {sorted(found)}")
    return fails


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
    return {"id": sc["id"], "pass": not fails, "fails": fails, "turns": sc["turns"], "replies": replies,
            "requests": req, "input_tokens": tin, "output_tokens": tout,
            "seconds": round(time.monotonic() - t0, 1), "turn_seconds": turn_secs}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--modes", nargs="+", default=["standard"], choices=["minimal", "standard", "guardrails"])
    ap.add_argument("--only", nargs="*", help="scenario ids to run")
    ap.add_argument("--price-in", type=float, help="$ per 1M input tokens (default: PRICES table)")
    ap.add_argument("--price-out", type=float, help="$ per 1M output tokens (default: PRICES table)")
    ap.add_argument("--budget", type=float, default=2.0, help="stop at this estimated $ total")
    args = ap.parse_args()

    spec = yaml.safe_load((ROOT / "evals/scenarios.yaml").read_text())
    now = datetime.fromisoformat(spec["today"]).replace(tzinfo=TZ)
    scenarios = [s for s in spec["scenarios"] if not args.only or s["id"] in args.only]
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
            results = []
            for sc in scenarios:
                if spent >= args.budget:
                    print(f"budget reached (${spent:.3f}); stopping")
                    break
                res = await run_scenario(sc, model, now, mode)
                cost = res["input_tokens"] / 1e6 * p_in + res["output_tokens"] / 1e6 * p_out
                spent += cost
                res["cost"] = round(cost, 5)
                results.append(res)
                print(f"[{label}] {sc['id']:<28} {'PASS' if res['pass'] else 'FAIL'}  ${cost:.4f}  {res['seconds']}s  {'; '.join(res['fails'])}")
            n_pass = sum(r["pass"] for r in results)
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{name}-{mode}")
            (out_dir / f"{stamp}-{safe}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
            lines = [f"# Eval: {label}", "", f"Passed {n_pass}/{len(results)}. Est. cost ${sum(r['cost'] for r in results):.4f}.", ""]
            for r in results:
                lines.append(f"## {r['id']} — {'PASS' if r['pass'] else 'FAIL'}")
                if r["fails"]:
                    lines.append(f"Failed: {'; '.join(r['fails'])}")
                for u, a in zip(r["turns"], r["replies"]):
                    lines += ["", f"> **User:** {u}", "", f"**Ridely:** {a}"]
                lines.append("")
            (out_dir / f"{stamp}-{safe}.md").write_text("\n".join(lines))
            secs = sorted(t for r in results for t in r.get("turn_seconds", []))
            med = secs[len(secs) // 2] if secs else 0
            mx = secs[-1] if secs else 0
            summary.append((label, n_pass, len(results), sum(r["cost"] for r in results), med, mx))
            print(f"== {label}: {n_pass}/{len(results)} passed. Total spent so far ${spent:.3f}")
    print("\nSummary:")
    for label, n, total, cost, med, mx in summary:
        print(f"  {label:<50} {n}/{total}  ${cost:.4f}  reply median {med}s, max {mx}s")

if __name__ == "__main__":
    asyncio.run(main())
