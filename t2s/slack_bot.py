"""Slack bot: mention the bot (or DM it) with a question, get the answer, SQL and table in a thread.

Runs in Socket Mode, so it needs no public URL -- it works from a laptop or any
server behind a firewall.

    export SLACK_BOT_TOKEN=xoxb-...      # OAuth & Permissions page
    export SLACK_APP_TOKEN=xapp-...      # Basic Information -> App-Level Tokens (connections:write)
    export ANTHROPIC_API_KEY=sk-ant-...
    export T2S_DB=postgresql+psycopg://readonly:pw@host/db    # or a SQLite path
    python -m t2s.slack_bot

Create the Slack app from ``slack_manifest.yml`` (api.slack.com/apps -> From a manifest).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

from .agent import AgentResult, TextToSQLAgent
from .sqldb import open_database

log = logging.getLogger("t2s.slack")

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "chinook.db"
MAX_TABLE_ROWS = 15
MAX_CELL = 40


def render_table(columns: list[str], rows: list[tuple], max_rows: int = MAX_TABLE_ROWS) -> str:
    """Fixed-width text table that lines up inside a Slack code block."""

    def cell(v) -> str:
        s = "NULL" if v is None else str(v)
        return s if len(s) <= MAX_CELL else s[: MAX_CELL - 1] + "…"

    shown = [[cell(v) for v in r] for r in rows[:max_rows]]
    widths = [max([len(c)] + [len(r[i]) for r in shown]) for i, c in enumerate(columns)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths)).rstrip()  # noqa: E731
    out = [line(columns), line(["-" * w for w in widths])] + [line(r) for r in shown]
    if len(rows) > max_rows:
        out.append(f"... {len(rows) - max_rows} more rows")
    return "\n".join(out)


def render_blocks(res: AgentResult) -> list[dict]:
    """Slack Block Kit message for one answer."""
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": res.answer[:2900]}}]
    if res.result and res.result.columns and res.result.rows and len(res.result.rows) > 1:
        table = render_table(res.result.columns, res.result.rows)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{table[:2800]}```"}})
    if res.sql:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*SQL used*\n```{res.sql[:2800]}```"}})
    status = "" if res.ok else ":warning: not fully answered · "
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"{status}{len(res.steps)} steps · read-only query"}],
    })
    return blocks


def _clean(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


def build_app(agent: TextToSQLAgent):
    from slack_bolt import App

    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    lock = threading.Lock()  # one question at a time per process keeps DB load predictable

    def answer(question: str, channel: str, thread_ts: str, client) -> None:
        if not question:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                    text="Ask me a question about the data, e.g. _top 5 customers by revenue this year_.")
            return
        placeholder = client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                              text=":hourglass_flowing_sand: Looking at the data...")
        try:
            with lock:
                res = agent.ask(question)
            client.chat_update(channel=channel, ts=placeholder["ts"], text=res.answer[:3000],
                               blocks=render_blocks(res))
        except Exception as e:  # never leave the user staring at "Looking..."
            log.exception("question failed")
            client.chat_update(channel=channel, ts=placeholder["ts"],
                               text=f":x: Something went wrong: {type(e).__name__}. The error is in the bot's log.")

    @app.event("app_mention")
    def on_mention(event, client):
        answer(_clean(event.get("text")), event["channel"], event.get("thread_ts") or event["ts"], client)

    @app.event("message")
    def on_dm(event, client):
        if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype"):
            return
        answer(_clean(event.get("text")), event["channel"], event.get("thread_ts") or event["ts"], client)

    return app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    missing = [k for k in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "ANTHROPIC_API_KEY") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)} (see t2s/slack_bot.py)")

    from slack_bolt.adapter.socket_mode import SocketModeHandler

    db = open_database(os.environ.get("T2S_DB", str(DEFAULT_DB)), schema=os.environ.get("T2S_SCHEMA"))
    agent = TextToSQLAgent(db, effort=os.environ.get("T2S_EFFORT", "medium"))
    log.info("Connected to %s", db.label)
    SocketModeHandler(build_app(agent), os.environ["SLACK_APP_TOKEN"]).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
