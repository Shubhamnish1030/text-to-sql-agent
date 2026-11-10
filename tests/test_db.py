import sqlite3

import pytest

from t2s.db import UnsafeQueryError, check_read_only, format_result


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from Track;",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT 'drop table x; delete' AS s",          # keywords inside a string are fine
    "SELECT LastName -- update comment\nFROM Customer",
    "SELECT replace(Name, 'a', 'b') FROM Artist",  # replace() function is allowed
])
def test_allows_read_only(sql):
    assert check_read_only(sql)


@pytest.mark.parametrize("sql", [
    "",
    "DELETE FROM Track",
    "DROP TABLE Track",
    "UPDATE Track SET Name = 'x'",
    "INSERT INTO Genre VALUES (99, 'x')",
    "SELECT 1; DROP TABLE Track",
    "PRAGMA table_info(Track)",
    "ATTACH DATABASE 'x.db' AS x",
    "WITH x AS (SELECT 1) DELETE FROM Track",
])
def test_blocks_unsafe(sql):
    with pytest.raises(UnsafeQueryError):
        check_read_only(sql)


def test_connection_is_read_only_even_if_guard_bypassed(db):
    with pytest.raises(sqlite3.OperationalError):
        db.conn.execute("DELETE FROM Genre")


def test_list_and_describe(db):
    tables = db.list_tables()
    assert "Track" in tables and "Invoice" in tables
    desc = db.describe_table("track")  # case-insensitive
    assert "AlbumId -> Album.AlbumId" in desc
    with pytest.raises(ValueError):
        db.describe_table("NoSuchTable")


def test_row_cap(db):
    res = db.run_query("SELECT TrackId FROM Track", max_rows=10)
    assert len(res.rows) == 10 and res.truncated
    assert "10+ rows" in format_result(res)


def test_timeout(db):
    db.timeout_s = 0.2
    slow = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
            "SELECT COUNT(*) FROM c")
    with pytest.raises(TimeoutError):
        db.run_query(slow)
    # connection still usable afterwards
    assert db.run_query("SELECT 1").rows == [(1,)]
