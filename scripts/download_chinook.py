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


def main() -> int:
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
