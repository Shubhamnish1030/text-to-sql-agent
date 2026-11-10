"""Run the agent over evals/questions.json and report execution accuracy.

    python evals/run_eval.py                 # all questions
    python evals/run_eval.py --limit 5       # first 5
    python evals/run_eval.py --ids q07 q15   # specific questions

Each run makes real API calls (roughly 4-8 model calls per question).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from t2s import Database, TextToSQLAgent  # noqa: E402
from t2s.agent import DEFAULT_MODEL  # noqa: E402
from t2s.compare import results_match  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(ROOT / "data" / "chinook.db"))
    p.add_argument("--questions", default=str(ROOT / "evals" / "questions.json"))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default="medium")
    p.add_argument("--limit", type=int)
    p.add_argument("--ids", nargs="*")
    args = p.parse_args()

    questions = json.loads(Path(args.questions).read_text())
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    if args.limit:
        questions = questions[: args.limit]

    db = Database(args.db)
    agent = TextToSQLAgent(db, model=args.model, effort=args.effort)
    records, passed = [], 0
    tokens_in = tokens_out = 0
    t0 = time.time()

    for q in questions:
        gold = db.run_query(q["gold_sql"], max_rows=10_000).rows
        start = time.time()
        try:
            res = agent.ask(q["question"])
            pred = db.run_query(res.sql, max_rows=10_000).rows if res.sql else None
            ok = pred is not None and results_match(gold, pred)
            err = None
        except Exception as e:  # keep going; record the failure
            res, ok, err = None, False, f"{type(e).__name__}: {e}"
        passed += ok
        if res:
            tokens_in += res.input_tokens
            tokens_out += res.output_tokens
        print(f"{'PASS' if ok else 'FAIL'}  {q['id']} [{q['level']}] {q['question']}")
        if not ok:
            print(f"      predicted: {res.sql if res else err}")
        records.append({
            "id": q["id"],
            "level": q["level"],
            "question": q["question"],
            "pass": ok,
            "predicted_sql": res.sql if res else None,
            "answer": res.answer if res else None,
            "error": err,
            "tool_calls": len(res.steps) if res else 0,
            "seconds": round(time.time() - start, 1),
        })

    n = len(questions)
    by_level = {}
    for r in records:
        s = by_level.setdefault(r["level"], [0, 0])
        s[0] += r["pass"]
        s[1] += 1
    print(f"\nExecution accuracy: {passed}/{n} = {passed / n:.0%}")
    for lvl, (ok, tot) in by_level.items():
        print(f"  {lvl:<6} {ok}/{tot}")
    print(f"Tokens: {tokens_in} in / {tokens_out} out   Time: {time.time() - t0:.0f}s")

    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{args.model}-{args.effort}.json"
    out.write_text(json.dumps({"model": args.model, "effort": args.effort, "accuracy": passed / n,
                               "tokens_in": tokens_in, "tokens_out": tokens_out, "results": records}, indent=2))
    print(f"Saved {out.relative_to(ROOT)}")
    return 0 if passed == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
