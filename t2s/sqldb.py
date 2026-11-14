"""Read-only access to PostgreSQL and MySQL, with the same interface as ``Database``.

Same three layers as the SQLite backend, adapted to a server database:
1. Every connection is put in read-only mode (``default_transaction_read_only``
   on Postgres, ``SET SESSION TRANSACTION READ ONLY`` on MySQL), and the agent
   should also log in as a user that only has SELECT grants (see README).
2. ``check_read_only`` rejects anything but a single SELECT/WITH statement,
   plus clauses that write files or take locks (INTO OUTFILE, FOR UPDATE, ...).
3. A server-side statement timeout and a client-side row cap.
"""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event

from .db import DEFAULT_MAX_ROWS, DEFAULT_TIMEOUT_S, QueryResult, check_read_only

_LABELS = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MySQL/MariaDB"}


class ServerDatabase:
    """A read-only handle on a PostgreSQL or MySQL database, opened from a SQLAlchemy URL.

    Examples::

        postgresql+psycopg://readonly:pw@host:5432/shop
        mysql+pymysql://readonly:pw@host:3306/shop
    """

    def __init__(self, url: str, timeout_s: float = DEFAULT_TIMEOUT_S, schema: str | None = None):
        self.url = sa.make_url(url)
        self.dialect = self.url.get_backend_name()
        if self.dialect not in _LABELS:
            raise ValueError(f"Unsupported database '{self.dialect}'. Use postgresql or mysql.")
        self.dialect_label = _LABELS[self.dialect]
        self.timeout_s = timeout_s
        self.schema = schema
        self.engine = sa.create_engine(self.url, pool_pre_ping=True, pool_size=2, max_overflow=2)
        event.listen(self.engine, "connect", self._on_connect)
        self._tables: list[str] | None = None

    @property
    def label(self) -> str:
        return f"{self.url.database} ({self.dialect_label} at {self.url.host})"

    def _on_connect(self, dbapi_conn: Any, _record: Any) -> None:
        ms = int(self.timeout_s * 1000)
        cur = dbapi_conn.cursor()
        if self.dialect == "postgresql":
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(f"SET statement_timeout = {ms}")
        else:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(f"SET SESSION max_execution_time = {ms}")
        cur.close()
        dbapi_conn.commit()

    def close(self) -> None:
        self.engine.dispose()

    # --- schema exploration -------------------------------------------------

    def _inspector(self) -> sa.Inspector:
        return sa.inspect(self.engine)

    def list_tables(self) -> list[str]:
        if self._tables is None:
            insp = self._inspector()
            names = insp.get_table_names(schema=self.schema) + insp.get_view_names(schema=self.schema)
            self._tables = sorted(set(names), key=str.lower)
        return list(self._tables)

    def _require_table(self, table: str) -> str:
        lookup = {t.lower(): t for t in self.list_tables()}
        if table.lower() not in lookup:
            raise ValueError(f"Unknown table '{table}'. Available: {', '.join(lookup.values())}")
        return lookup[table.lower()]

    def _quote(self, table: str) -> str:
        prep = self.engine.dialect.identifier_preparer
        q = prep.quote(table)
        return f"{prep.quote_schema(self.schema)}.{q}" if self.schema else q

    def _estimated_rows(self, table: str) -> int | None:
        """Planner statistics instead of COUNT(*), which can take minutes on big tables."""
        if self.dialect == "postgresql":
            # reltuples is -1 until the table is first ANALYZEd; fall back to the stats collector.
            sql = ("SELECT CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint ELSE s.n_live_tup END "
                   "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                   "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
                   "WHERE c.relname = :t AND n.nspname = COALESCE(:s, current_schema())")
        else:
            sql = ("SELECT table_rows FROM information_schema.tables "
                   "WHERE table_name = :t AND table_schema = COALESCE(:s, DATABASE())")
        try:
            with self.engine.connect() as conn:
                n = conn.execute(sa.text(sql), {"t": table, "s": self.schema}).scalar()
            return int(n) if n is not None and int(n) >= 0 else None
        except Exception:
            return None

    def describe_table(self, table: str) -> str:
        table = self._require_table(table)
        insp = self._inspector()
        cols = insp.get_columns(table, schema=self.schema)
        pk = set(insp.get_pk_constraint(table, schema=self.schema).get("constrained_columns") or [])
        fks = insp.get_foreign_keys(table, schema=self.schema)
        rows = self._estimated_rows(table)

        size = f"~{rows} rows" if rows is not None else "row count unknown"
        lines = [f"Table {table} ({size})", "Columns:"]
        for c in cols:
            flags = []
            if c["name"] in pk:
                flags.append("PRIMARY KEY")
            if not c.get("nullable", True):
                flags.append("NOT NULL")
            line = f"  - {c['name']} {c['type']} {' '.join(flags)}".rstrip()
            if c.get("comment"):
                line += f"  -- {c['comment']}"
            lines.append(line)
        if fks:
            lines.append("Foreign keys:")
            for fk in fks:
                for src, dst in zip(fk["constrained_columns"], fk["referred_columns"]):
                    lines.append(f"  - {src} -> {fk['referred_table']}.{dst}")
        return "\n".join(lines)

    def sample_rows(self, table: str, limit: int = 3) -> QueryResult:
        table = self._require_table(table)
        limit = max(1, min(int(limit), 10))
        return self.run_query(f"SELECT * FROM {self._quote(table)} LIMIT {limit}")

    # --- query execution ----------------------------------------------------

    def run_query(self, sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> QueryResult:
        sql = check_read_only(sql)
        # Raw DB-API cursor with no parameters: drivers don't treat '%' or ':x'
        # in the SQL as placeholders, so LIKE 'a%' works as written.
        conn = self.engine.raw_connection()
        start = time.monotonic()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                rows = [tuple(r) for r in cur.fetchmany(max_rows + 1)]
                columns = [d[0] for d in cur.description] if cur.description else []
            finally:
                cur.close()
        except Exception as e:
            if _is_timeout(e):
                raise TimeoutError(f"Query exceeded {self.timeout_s:.0f}s timeout.") from e
            raise
        finally:
            try:
                conn.rollback()  # end the read-only transaction either way
            finally:
                conn.close()
        elapsed = (time.monotonic() - start) * 1000
        truncated = len(rows) > max_rows
        return QueryResult(columns, rows[:max_rows], truncated, elapsed)


def _is_timeout(e: Exception) -> bool:
    msg = str(e).lower()
    code = getattr(e, "args", [None])[0]
    return (
        type(e).__name__ == "QueryCanceled"                  # psycopg: statement_timeout
        or "statement timeout" in msg
        or code == 3024                                       # MySQL: max_execution_time
        or "maximum statement execution time exceeded" in msg
    )


def open_database(target: str, timeout_s: float = DEFAULT_TIMEOUT_S, schema: str | None = None):
    """A SQLite file path, or a postgresql:// / mysql:// URL."""
    from .db import Database

    if "://" in target:
        return ServerDatabase(target, timeout_s=timeout_s, schema=schema)
    return Database(target, timeout_s=timeout_s)
