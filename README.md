# Text-to-SQL Agent

Ask questions about a database in plain English, from the terminal, a web UI or **Slack**. Works with **PostgreSQL, MySQL/MariaDB and SQLite**. The agent explores the schema, writes SQL, runs it, fixes its own mistakes, and answers with both the result and the query it used.

```
$ python -m t2s "Which sales agent brought in the most revenue?"
  > list_tables
  > describe_table({"table": "Employee"})
  > describe_table({"table": "Customer"})
  > describe_table({"table": "Invoice"})
  > run_query: SELECT e.FirstName, e.LastName, ROUND(SUM(i.Total), 2) AS revenue FROM Employee e JOIN ...
  > final_answer: SELECT e.FirstName, e.LastName, ROUND(SUM(i.Total), 2) AS revenue FROM Employee e JOIN ...

Jane Peacock brought in the most revenue: $833.04 from the customers she supports.
```

Built with the [Claude API](https://docs.claude.com) tool use and a hand-written agent loop. Demo data is the [Chinook](https://github.com/lerocha/chinook-database) music-store database (11 tables: artists, albums, tracks, customers, invoices, ...).

## How it works

```
question ──► ┌──────────────────────── agent loop ────────────────────────┐
             │  model ──tool call──► list_tables / describe_table /      │
             │    ▲                  sample_rows / run_query              │
             │    └────result or error───────────┘                        │
             │  ...repeats until the model calls final_answer(sql, answer)│
             └────────────────────────────────────────────────────────────┘
                                   │
                    answer + SQL + full result table
```

1. **Explore.** The model doesn't get the whole schema up front. It lists tables and describes only the ones it needs, the same way a person would. This keeps context small and still works on large databases.
2. **Test.** It runs candidate queries with `run_query` and sees real results (capped at 50 rows).
3. **Self-correct.** SQL errors (unknown column, syntax) go back to the model as `is_error` tool results, and it fixes the query.
4. **Answer.** It calls `final_answer` with the SQL and a plain-language answer. The SQL runs once more, and if it fails, the model gets the error and tries again.

The loop is in [`t2s/agent.py`](t2s/agent.py) (~150 lines). It's written by hand rather than with an SDK helper so every step is visible.

## Safety

Model-written SQL is untrusted input, so independent layers guard it ([`t2s/db.py`](t2s/db.py), [`t2s/sqldb.py`](t2s/sqldb.py)):

| Layer | SQLite | PostgreSQL / MySQL |
|---|---|---|
| Database login | n/a | Use a login with **SELECT grants only** (setup below). This is the real guarantee. |
| Read-only session | `mode=ro` + `PRAGMA query_only` | `default_transaction_read_only = on` / `SET SESSION TRANSACTION READ ONLY` on every pooled connection. Writes fail even if the login has write grants (tested). |
| Query validator | Single `SELECT`/`WITH` only; rejects `DELETE`, `DROP`, `PRAGMA`, stacked `;` ... | Same, plus clauses that write files, lock rows or leave the DB inside a SELECT: `INTO OUTFILE`, `SELECT INTO`, `FOR UPDATE`, `pg_read_file`, `LOAD_FILE`, `dblink`, `set_config`, `nextval`, `pg_sleep` ... |
| Resource limits | 5 s timeout (progress handler), row cap | 5 s server-side `statement_timeout` / `max_execution_time`, row cap |

String literals and comments are stripped before validation, so `WHERE name = 'drop; it'` isn't a false positive.

## Quick start

```bash
git clone https://github.com/Shubhamnish1030/text-to-sql-agent.git
cd text-to-sql-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/download_chinook.py          # ~1 MB sample database

export ANTHROPIC_API_KEY=sk-ant-...          # https://console.anthropic.com
```

**CLI**

```bash
python -m t2s "Which 5 artists have the most albums?"
python -m t2s                                # interactive mode
python -m t2s --db path/to/your.db "..."     # any SQLite database
python -m t2s --effort low "..."             # cheaper/faster
```

**Your own PostgreSQL / MySQL database**

```bash
python -m t2s --db "postgresql+psycopg://analyst:PASSWORD@host:5432/shop" "revenue by month this year"
python -m t2s --db "mysql+pymysql://analyst:PASSWORD@host:3306/shop" "top 10 customers by lifetime value"
export T2S_DB="postgresql+psycopg://..."     # or set it once for the CLI, web UI and Slack bot
```

Create a read-only login first:

```sql
-- PostgreSQL
CREATE ROLE analyst LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE shop TO analyst;
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;
ALTER ROLE analyst SET default_transaction_read_only = on;

-- MySQL
CREATE USER 'analyst'@'%' IDENTIFIED BY '...';
GRANT SELECT ON shop.* TO 'analyst'@'%';
```

Point it at a read replica if there is one, so analytics questions never compete with production traffic.

**Demo Postgres in Docker**

```bash
python scripts/download_chinook.py --postgres
docker compose up -d
python -m t2s --db postgresql+psycopg://analyst:analyst@localhost:5433/chinook "top 5 genres by revenue"
```

**Slack bot**

Mention the bot in a channel or DM it. It replies in a thread with the answer, a result table and the SQL it ran.

```
@data which 3 countries have spent the most?

  The USA has spent the most ($523.06), then Canada ($303.96) and France ($195.10).
  country  total
  -------  ------
  USA      523.06
  Canada   303.96
  France   195.10
  SQL used: SELECT billing_country AS country, SUM(total) AS total FROM invoice ...
```

1. [api.slack.com/apps](https://api.slack.com/apps) → *Create New App* → *From a manifest* → paste [`slack_manifest.yml`](slack_manifest.yml).
2. Install it to the workspace (copy the `xoxb-` bot token), then under *Basic Information* create an app-level token with `connections:write` (`xapp-`).
3. Run it. Socket Mode means no public URL or open port is needed.

```bash
export SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... ANTHROPIC_API_KEY=sk-ant-...
export T2S_DB="postgresql+psycopg://analyst:...@host/shop"
python -m t2s.slack_bot
```

**Web UI**

```bash
streamlit run app.py
```

Shows the agent's steps live, the answer, the SQL and the result table.

## Evaluation

[`evals/questions.json`](evals/questions.json) has 20 questions (easy / medium / hard), each with a reference SQL query. The eval measures **execution accuracy**: does the agent's query return the same data as the reference query? Comparing SQL text would be wrong, because many different queries are equivalent.

The comparison ([`t2s/compare.py`](t2s/compare.py)) ignores row order, rounds floats, and allows extra columns (e.g. the agent returns `genre, track_count` when only `genre` was asked for).

```bash
python evals/run_eval.py              # all 20 questions (real API calls)
python evals/run_eval.py --limit 5
python evals/run_eval.py --effort low # compare settings
```

Results are saved to `evals/results/` with per-question SQL, answers, tool-call counts and token usage, so you can compare models and effort levels.

## Tests

```bash
pytest
```

No API key needed: the safety guard (allowed vs blocked queries, read-only enforcement, timeout, row cap), the result comparison, Slack message rendering, and the agent loop driven by a scripted fake client (error recovery, blocked writes, turn limit, refusal handling, request shape).

PostgreSQL / MySQL tests run against real servers when given a URL with a **write-capable** user, to prove the session-level read-only mode blocks writes on its own (CI runs both):

```bash
T2S_TEST_PG_URL=postgresql+psycopg://rw:rw@localhost:5433/chinook \
T2S_TEST_MYSQL_URL=mysql+pymysql://rw:rw@localhost:3306/Chinook pytest
```

## Project layout

```
t2s/
  agent.py      agent loop, system prompt
  tools.py      tool schemas + dispatch
  db.py         SQLite access, SQL guard, limits
  sqldb.py      PostgreSQL / MySQL access (SQLAlchemy), same interface
  slack_bot.py  Slack bot (Socket Mode)
  compare.py    execution-accuracy result matching
  __main__.py   CLI
app.py          Streamlit UI
docker-compose.yml  demo Postgres with Chinook + read-only login
slack_manifest.yml  one-paste Slack app setup
evals/          questions + eval runner
tests/          offline unit tests
```

## Design choices

- **Tools instead of pasting the schema.** Pasting the whole schema into the prompt works for 11 tables but not for 500. Exploring through tools scales and mirrors how analysts work.
- **`final_answer` tool instead of parsing text.** The final SQL comes back as a structured field, so it can be re-run, shown and evaluated reliably. `strict: true` on every tool guarantees schema-valid arguments.
- **Errors are data.** Tool failures go back to the model (`is_error: true`) instead of crashing the loop, which is what makes self-correction possible.
- **Prompt caching.** Each turn re-sends a growing prefix (tools + system + history), so caching cuts the cost of multi-step questions.

## Roadmap

- [x] MySQL / PostgreSQL support (read-only user + read-only session)
- [x] Slack bot
- [ ] Business glossary (e.g. "active customer" = ordered in last 90 days) injected into the prompt
- [ ] Schema retrieval (embeddings) for databases with hundreds of tables
- [ ] Larger benchmark (Spider / BIRD dev subset)
- [ ] Charts for numeric results
- [ ] Expose as an MCP server so Claude Desktop and other clients can use it

## License

MIT
