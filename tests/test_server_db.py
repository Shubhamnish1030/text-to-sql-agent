"""PostgreSQL / MySQL backend tests against a real server.

Skipped unless a URL is given. The URL's user should have write grants, so the
tests prove the *session* read-only mode blocks writes, not just missing grants:

    T2S_TEST_MYSQL_URL=mysql+pymysql://rw:rw@127.0.0.1:3306/Chinook pytest tests/test_server_db.py
    T2S_TEST_PG_URL=postgresql+psycopg://rw:rw@127.0.0.1:5432/chinook pytest tests/test_server_db.py

Both expect the Chinook sample database (see docker-compose.yml).
"""

import os

import pytest

URLS = [os.environ.get("T2S_TEST_MYSQL_URL"), os.environ.get("T2S_TEST_PG_URL")]


@pytest.fixture(params=[u for u in URLS if u] or [None], ids=lambda u: (u or "none").split(":")[0])
def sdb(request):
    if not request.param:
        pytest.skip("Set T2S_TEST_MYSQL_URL or T2S_TEST_PG_URL to run server tests.")
    from t2s import ServerDatabase

    d = ServerDatabase(request.param, timeout_s=1)
    yield d
    d.close()


def _genre(sdb):
    return next(t for t in sdb.list_tables() if t.lower() == "genre")


def test_schema_tools(sdb):
    tables = [t.lower() for t in sdb.list_tables()]
    assert "track" in tables and "invoice" in tables
    desc = sdb.describe_table("TRACK").lower().replace("_", "")  # case-insensitive lookup
    assert "primary key" in desc and "-> album.albumid" in desc
    assert "rows" in desc.splitlines()[0]
    assert len(sdb.sample_rows(_genre(sdb), 2).rows) == 2


def test_percent_and_colon_are_not_placeholders(sdb):
    g = _genre(sdb)
    q = sdb._quote(g)
    res = sdb.run_query(f"SELECT COUNT(*) FROM {q} WHERE 'a%:x' LIKE 'a%' AND ':y' <> ''")
    assert res.rows == [(25,)]


def test_session_is_read_only_even_with_write_grants(sdb):
    conn = sdb.engine.raw_connection()
    try:
        with pytest.raises(Exception, match=r"(?i)read.only"):
            conn.cursor().execute(f"DELETE FROM {sdb._quote(_genre(sdb))}")
    finally:
        conn.rollback()
        conn.close()
    assert sdb.run_query(f"SELECT COUNT(*) FROM {sdb._quote(_genre(sdb))}").rows == [(25,)]


def test_timeout_and_recovery(sdb):
    t = sdb._quote(next(x for x in sdb.list_tables() if x.lower() == "track"))
    slow = f"SELECT SUM(a.Milliseconds % (b.TrackId + c.TrackId)) FROM {t} a, {t} b, {t} c"
    if sdb.dialect == "postgresql":
        slow = slow.replace("Milliseconds", "milliseconds").replace("TrackId", "track_id")
    with pytest.raises(TimeoutError):
        sdb.run_query(slow)
    assert sdb.run_query("SELECT 1").rows == [(1,)]
