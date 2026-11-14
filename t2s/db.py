"""Read-only database access with safety guards.

Three layers of defence keep the agent from changing data:
1. The SQLite file is opened with ``mode=ro`` and ``PRAGMA query_only`` -- the
   engine itself refuses writes.
2. ``check_read_only`` rejects anything that is not a single SELECT/WITH
   statement before it reaches the engine.
3. Every query gets a row cap and a wall-clock timeout so a bad query cannot
   hang the app or flood the model's context.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_ROWS = 200
DEFAULT_TIMEOUT_S = 5.0

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|"
    r"reindex|truncate|grant|revoke|begin|commit|rollback|savepoint|"
    # Clauses/functions that write files, take locks, change settings or reach
    # outside the database even inside a SELECT (Postgres / MySQL).
    r"into|outfile|dumpfile|load_file|copy|set|lock|call|exec|execute|handler|"
    r"pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|"
    r"dblink|dblink_exec|set_config|setval|nextval|pg_terminate_backend|"
    r"pg_cancel_backend|pg_sleep|sleep|benchmark)\b"
    r"|\bfor\s+(?:update|share|no\s+key\s+update|key\s+share)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(ValueError):
    """Raised when a query is not a single read-only statement."""


def _strip_comments_and_literals(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Blank out string literals so keywords or ';' inside them don't trip the checks.
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def check_read_only(sql: str) -> str:
    """Return the cleaned SQL if it is a single SELECT/WITH statement, else raise."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")
    cleaned = sql.strip().rstrip(";").strip()
    scan = _strip_comments_and_literals(cleaned)
    if ";" in scan:
        raise UnsafeQueryError("Only one statement is allowed per query.")
    if not re.match(r"^\s*(select|with)\b", scan, re.IGNORECASE):
        raise UnsafeQueryError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    match = _FORBIDDEN.search(scan)
    if match:
        word = (match.group(1) or match.group(0)).upper()
        raise UnsafeQueryError(f"Keyword '{' '.join(word.split())}' is not allowed.")
    return cleaned


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float


class Database:
    """A read-only handle on a SQLite database file."""

    dialect = "sqlite"
    dialect_label = "SQLite"

    def __init__(self, path: str | Path, timeout_s: float = DEFAULT_TIMEOUT_S):
        path = Path(path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Database not found: {path}")
        self.path = path
        self.timeout_s = timeout_s
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.execute("PRAGMA query_only = ON")

    @property
    def label(self) -> str:
        return self.path.name

    def close(self) -> None:
        self.conn.close()

    # --- schema exploration -------------------------------------------------

    def list_tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def _require_table(self, table: str) -> str:
        lookup = {t.lower(): t for t in self.list_tables()}
        if table.lower() not in lookup:
            raise ValueError(f"Unknown table '{table}'. Available: {', '.join(lookup.values())}")
        return lookup[table.lower()]

    def describe_table(self, table: str) -> str:
        """Columns, types, keys, foreign keys and row count for one table."""
        table = self._require_table(table)
        q = '"' + table.replace('"', '""') + '"'
        cols = self.conn.execute(f"PRAGMA table_info({q})").fetchall()
        fks = self.conn.execute(f"PRAGMA foreign_key_list({q})").fetchall()
        count = self.conn.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]

        lines = [f"Table {table} ({count} rows)", "Columns:"]
        for _cid, name, ctype, notnull, _default, pk in cols:
            flags = []
            if pk:
                flags.append("PRIMARY KEY")
            if notnull:
                flags.append("NOT NULL")
            lines.append(f"  - {name} {ctype or ''} {' '.join(flags)}".rstrip())
        if fks:
            lines.append("Foreign keys:")
            for fk in fks:
                # (id, seq, table, from, to, on_update, on_delete, match)
                lines.append(f"  - {fk[3]} -> {fk[2]}.{fk[4]}")
        return "\n".join(lines)

    def sample_rows(self, table: str, limit: int = 3) -> QueryResult:
        table = self._require_table(table)
        limit = max(1, min(int(limit), 10))
        q = '"' + table.replace('"', '""') + '"'
        return self.run_query(f"SELECT * FROM {q} LIMIT {limit}")

    # --- query execution ----------------------------------------------------

    def run_query(self, sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> QueryResult:
        sql = check_read_only(sql)
        deadline = time.monotonic() + self.timeout_s
        # Called every N VM instructions; a non-zero return aborts the query.
        self.conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        start = time.monotonic()
        try:
            cur = self.conn.execute(sql)
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise TimeoutError(f"Query exceeded {self.timeout_s:.0f}s timeout.") from e
            raise
        finally:
            self.conn.set_progress_handler(None, 0)
        elapsed = (time.monotonic() - start) * 1000
        columns = [d[0] for d in cur.description] if cur.description else []
        truncated = len(rows) > max_rows
        return QueryResult(columns, rows[:max_rows], truncated, elapsed)


def format_result(result: QueryResult, max_display: int = 50) -> str:
    """Render a result as a compact pipe-separated table for the model."""
    if not result.columns:
        return "(no columns)"
    shown = result.rows[:max_display]
    lines = [" | ".join(result.columns)]
    for row in shown:
        lines.append(" | ".join("NULL" if v is None else str(v) for v in row))
    total = f"{len(result.rows)}{'+' if result.truncated else ''}"
    footer = f"({total} rows"
    if len(result.rows) > len(shown) or result.truncated:
        footer += f", showing first {len(shown)}"
    footer += ")"
    lines.append(footer)
    return "\n".join(lines)
