"""The agent loop: the model explores the schema, tests queries and submits an answer.

The loop is written by hand (not with the SDK's tool runner) so every step is
visible: call the model -> run the tools it asked for -> send results back ->
repeat until it calls ``final_answer``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

from .db import Database, QueryResult
from .tools import TOOLS, ToolError, describe_call, execute_tool

DEFAULT_MODEL = "claude-opus-5-5"
# If a safety classifier declines a request, the API retries it on a fallback
# model server-side instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You answer questions about data by writing SQLite queries against a read-only database.

How to work:
1. Explore before writing SQL: list the tables, describe the ones that look relevant, and sample rows when value formats matter (dates, IDs, text casing).
2. Test your query with run_query and check the result actually answers the question. If it errors or looks wrong, fix it and try again.
3. Finish by calling final_answer with the query and a short plain-language answer that quotes the key numbers from the result.

Query rules:
- SQLite dialect only. Use explicit JOINs on the foreign keys you have seen.
- Return only the columns the question needs, with readable aliases.
- For "top N" questions use ORDER BY ... LIMIT N. Round money to 2 decimals.
- If the question is ambiguous, pick the most reasonable reading and say which one you chose in the answer.
- If the data cannot answer the question, say so in final_answer and use a query that shows why."""


@dataclass
class Step:
    tool: str
    args: dict[str, Any]
    output: str
    is_error: bool

    def summary(self) -> str:
        return describe_call(self.tool, self.args)


@dataclass
class AgentResult:
    question: str
    answer: str
    sql: str | None = None
    result: QueryResult | None = None
    steps: list[Step] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    ok: bool = True


class TextToSQLAgent:
    def __init__(
        self,
        db: Database,
        client: anthropic.Anthropic | None = None,
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
        max_turns: int = 15,
    ):
        self.db = db
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_turns = max_turns

    def _call_model(self, messages: list[dict[str, Any]]):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},  # reuse the growing prefix across turns
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def ask(self, question: str, on_step: Callable[[Step], None] | None = None) -> AgentResult:
        out = AgentResult(question=question, answer="")
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        last_ok: tuple[str, QueryResult] | None = None  # fallback if final_answer never comes

        for _ in range(self.max_turns):
            response = self._call_model(messages)
            out.turns += 1
            out.input_tokens += response.usage.input_tokens
            out.output_tokens += response.usage.output_tokens

            if response.stop_reason == "refusal":
                out.answer, out.ok = "The model declined to answer this request.", False
                return out

            # Send the full content back (thinking blocks included) so the model keeps its context.
            messages.append({"role": "assistant", "content": response.content})
            tool_calls = [b for b in response.content if b.type == "tool_use"]

            if not tool_calls:
                # Model stopped without final_answer: fall back to its text + last good query.
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                out.answer = text or "No answer produced."
                if last_ok:
                    out.sql, out.result = last_ok
                out.ok = bool(text) and response.stop_reason == "end_turn"
                return out

            tool_results = []
            finished = False
            for call in tool_calls:
                args = dict(call.input or {})
                try:
                    text, result = execute_tool(self.db, call.name, args)
                    is_error = False
                except ToolError as e:
                    text, result, is_error = f"Error: {e}", None, True

                step = Step(call.name, args, text, is_error)
                out.steps.append(step)
                if on_step:
                    on_step(step)

                if not is_error and call.name in ("run_query", "final_answer") and result is not None:
                    last_ok = (args["sql"], result)
                if call.name == "final_answer" and not is_error:
                    out.answer, out.sql, out.result = args["answer"], args["sql"], result
                    finished = True

                tool_results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": text, "is_error": is_error}
                )

            if finished:
                return out
            # All results for one turn go back in a single user message.
            messages.append({"role": "user", "content": tool_results})

        out.answer, out.ok = f"Stopped after {self.max_turns} turns without a final answer.", False
        if last_ok:
            out.sql, out.result = last_ok
        return out
