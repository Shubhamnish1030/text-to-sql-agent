import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "chinook.db"


@pytest.fixture
def db():
    if not DB_PATH.exists():
        pytest.skip("Run `python scripts/download_chinook.py` first.")
    from t2s import Database

    d = Database(DB_PATH)
    yield d
    d.close()
