"""Download the Chinook sample database (a digital music store, MIT licensed)."""

import sqlite3
import sys
import urllib.request
from pathlib import Path

URL = (
    "https://github.com/lerocha/chinook-database/raw/master/"
    "ChinookDatabase/DataSources/Chinook_Sqlite.sqlite"
)
DEST = Path(__file__).resolve().parent.parent / "data" / "chinook.db"
PG_URL = (
    "https://github.com/lerocha/chinook-database/raw/master/"
    "ChinookDatabase/DataSources/Chinook_PostgreSql.sql"
)
PG_DEST = DEST.with_name("chinook_pg.sql")


def download_postgres() -> int:
    """SQL dump used by docker-compose.yml to seed the demo Postgres."""
    if PG_DEST.exists():
        print(f"Already present: {PG_DEST}")
        return 0
    PG_DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {PG_URL}")
    tmp = PG_DEST.with_suffix(".part")
    urllib.request.urlretrieve(PG_URL, tmp)
    if b"CREATE DATABASE chinook" not in tmp.read_bytes()[:5000]:
        tmp.unlink()
        print("Downloaded file does not look like the Chinook Postgres dump.", file=sys.stderr)
        return 1
    tmp.rename(PG_DEST)
    print(f"Saved {PG_DEST}")
    return 0


def main() -> int:
    if "--postgres" in sys.argv[1:]:
        return download_postgres()
    if DEST.exists():
        print(f"Already present: {DEST}")
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL}")
    tmp = DEST.with_suffix(".part")
    urllib.request.urlretrieve(URL, tmp)
    tables = sqlite3.connect(tmp).execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    if tables < 10:
        tmp.unlink()
        print("Downloaded file does not look like Chinook.", file=sys.stderr)
        return 1
    tmp.rename(DEST)
    print(f"Saved {DEST} ({tables} tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
