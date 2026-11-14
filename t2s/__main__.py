"""Command-line interface.

    python -m t2s "Which genre has the most tracks?"
    python -m t2s            # interactive mode
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anthropic

from .agent import DEFAULT_MODEL, AgentResult, Step, TextToSQLAgent
from .db import format_result
from .sqldb import open_database

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "chinook.db"


def _print_step(step: Step) -> None:
    mark = "x" if step.is_error else ">"
    print(f"  {mark} {step.summary()}", file=sys.stderr)
    if step.is_error:
        print(f"    {step.output}", file=sys.stderr)


def _print_result(res: AgentResult) -> None:
    print(f"\n{res.answer}\n")
    if res.sql:
        print("SQL:")
        print("  " + "\n  ".join(res.sql.splitlines()))
    if res.result:
        print("\n" + format_result(res.result, max_display=20))
    print(
        f"\n[{res.turns} model calls, {len(res.steps)} tool calls, "
        f"{res.input_tokens} in / {res.output_tokens} out tokens]",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="t2s", description="Ask questions about a SQLite, PostgreSQL or MySQL database.")
    p.add_argument("question", nargs="*", help="Question to ask (omit for interactive mode).")
    p.add_argument(
        "--db",
        default=os.environ.get("T2S_DB", str(DEFAULT_DB)),
        help="SQLite file path, or a URL like postgresql+psycopg://user:pw@host/db or "
        "mysql+pymysql://user:pw@host/db (default: $T2S_DB or the Chinook demo).",
    )
    p.add_argument("--schema", default=None, help="Postgres schema / MySQL database to explore.")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--quiet", action="store_true", help="Don't print the agent's tool calls.")
    args = p.parse_args(argv)

    db = open_database(args.db, schema=args.schema)
    agent = TextToSQLAgent(db, model=args.model, effort=args.effort)
    on_step = None if args.quiet else _print_step

    def ask(q: str) -> None:
        try:
            _print_result(agent.ask(q, on_step=on_step))
        except anthropic.AuthenticationError:
            sys.exit("Authentication failed: check ANTHROPIC_API_KEY (see README).")
        except TypeError as e:
            if "authentication" not in str(e).lower():
                raise
            sys.exit("No API key found: export ANTHROPIC_API_KEY=... (see README).")
        except anthropic.APIError as e:
            print(f"API error: {e}", file=sys.stderr)

    if args.question:
        ask(" ".join(args.question))
        return 0

    print(f"Connected to {db.label}. Ask a question (Ctrl-D to quit).")
    while True:
        try:
            q = input("\n? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if q:
            ask(q)


if __name__ == "__main__":
    raise SystemExit(main())
