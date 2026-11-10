# Text-to-SQL Agent

Ask questions about a database in plain English. The agent explores the schema, writes SQL, runs it, fixes its own mistakes, and answers with both the result and the query it used.

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

Model-written SQL is untrusted input, so three independent layers guard it ([`t2s/db.py`](t2s/db.py)):

| Layer | What it does |
|---|---|
| Read-only connection | SQLite opened with `mode=ro` and `PRAGMA query_only`. The engine refuses writes even if everything else fails. |
| Query validator | Only a single `SELECT`/`WITH` statement. Rejects `DELETE`, `DROP`, `ATTACH`, `PRAGMA`, stacked statements (`;`), etc. String literals and comments are stripped first, so `WHERE name = 'drop; it'` isn't a false positive. |
| Resource limits | 5 s timeout per query (SQLite progress handler) and a row cap, so a runaway query can't hang the app or flood the context. |

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

29 tests, no API key needed: the safety guard (allowed vs blocked queries, read-only enforcement, timeout, row cap), the result comparison, and the agent loop driven by a scripted fake client (error recovery, blocked writes, turn limit, refusal handling, request shape).

## Project layout

```
t2s/
  agent.py      agent loop, system prompt
  tools.py      tool schemas + dispatch
  db.py         read-only DB access, SQL guard, limits
  compare.py    execution-accuracy result matching
  __main__.py   CLI
app.py          Streamlit UI
evals/          questions + eval runner
tests/          offline unit tests
```

## Design choices

- **Tools instead of pasting the schema.** Pasting the whole schema into the prompt works for 11 tables but not for 500. Exploring through tools scales and mirrors how analysts work.
- **`final_answer` tool instead of parsing text.** The final SQL comes back as a structured field, so it can be re-run, shown and evaluated reliably. `strict: true` on every tool guarantees schema-valid arguments.
- **Errors are data.** Tool failures go back to the model (`is_error: true`) instead of crashing the loop, which is what makes self-correction possible.
- **Prompt caching.** Each turn re-sends a growing prefix (tools + system + history), so caching cuts the cost of multi-step questions.

## Roadmap

- [ ] MySQL / PostgreSQL support (read-only user + `SET SESSION TRANSACTION READ ONLY`)
- [ ] Schema retrieval (embeddings) for databases with hundreds of tables
- [ ] Larger benchmark (Spider / BIRD dev subset)
- [ ] Charts for numeric results
- [ ] Expose as an MCP server so Claude Desktop and other clients can use it

## License

MIT
