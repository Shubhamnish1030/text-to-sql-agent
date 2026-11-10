"""Tool definitions the model can call, and the code that runs them."""

from __future__ import annotations

import json
from typing import Any

from .db import Database, QueryResult, UnsafeQueryError, format_result


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_tables",
        "description": "List every table and view in the database. Call this first to see what data exists.",
        "strict": True,
        "input_schema": _schema({}, []),
    },
    {
        "name": "describe_table",
        "description": (
            "Show a table's columns, types, primary key, foreign keys and row count. "
            "Use it before writing SQL that touches the table."
        ),
        "strict": True,
        "input_schema": _schema(
            {"table": {"type": "string", "description": "Exact table name from list_tables."}},
            ["table"],
        ),
    },
    {
        "name": "sample_rows",
        "description": "Return a few example rows from a table, to see real value formats (dates, codes, casing).",
        "strict": True,
        "input_schema": _schema(
            {
                "table": {"type": "string", "description": "Exact table name."},
                "limit": {"type": "integer", "description": "Rows to return, 1-10."},
            },
            ["table", "limit"],
        ),
    },
    {
        "name": "run_query",
        "description": (
            "Run one read-only SQLite SELECT query and see the result (up to 50 rows shown). "
            "Use it to test and refine queries. Writes are blocked."
        ),
        "strict": True,
        "input_schema": _schema(
            {"sql": {"type": "string", "description": "A single SELECT or WITH ... SELECT statement."}},
            ["sql"],
        ),
    },
    {
        "name": "final_answer",
        "description": (
            "Submit the final SQL query and a plain-language answer for the user. "
            "The SQL is run once more and its full result is shown to the user. Call this exactly once, last."
        ),
        "strict": True,
        "input_schema": _schema(
            {
                "sql": {"type": "string", "description": "The query whose result answers the question."},
                "answer": {
                    "type": "string",
                    "description": "A short answer in plain language, based on the query result.",
                },
            },
            ["sql", "answer"],
        ),
    },
]


class ToolError(Exception):
    """An expected failure the model should see and recover from."""


def execute_tool(db: Database, name: str, args: dict[str, Any]) -> tuple[str, QueryResult | None]:
    """Run one tool call. Returns (text for the model, query result if any).

    Raises ToolError for problems the model can fix (bad SQL, unknown table).
    """
    try:
        if name == "list_tables":
            return "\n".join(db.list_tables()), None
        if name == "describe_table":
            return db.describe_table(args["table"]), None
        if name == "sample_rows":
            result = db.sample_rows(args["table"], args.get("limit", 3))
            return format_result(result), result
        if name in ("run_query", "final_answer"):
            result = db.run_query(args["sql"])
            return format_result(result), result
    except (UnsafeQueryError, ValueError, TimeoutError) as e:
        raise ToolError(str(e)) from e
    except Exception as e:  # sqlite3 errors: syntax, unknown column, ...
        raise ToolError(f"{type(e).__name__}: {e}") from e
    raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(t['name'] for t in TOOLS)}")


def describe_call(name: str, args: dict[str, Any]) -> str:
    """One-line human-readable summary of a tool call (for logs/UI)."""
    if name in ("run_query", "final_answer"):
        return f"{name}: {' '.join(args.get('sql', '').split())}"
    if not args:
        return name
    return f"{name}({json.dumps(args)})"
