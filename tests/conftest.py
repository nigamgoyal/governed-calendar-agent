import os
import sys
from pathlib import Path

os.environ.setdefault("GCA_DSN", "dbname=governed_calendar_test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from gca import config, db, preferences, providers, seed


@pytest.fixture
def ctx():
    db.reset()
    cfg = config.load()
    conn_cm = db.conn()
    c = conn_cm.__enter__()
    preferences.seed(c)
    ws = seed.seed_week(c)
    cal, tsk = providers.calendar(cfg, c), providers.tasks(cfg, c)
    try:
        yield {"c": c, "cfg": cfg, "cal": cal, "tasks": tsk, "week_start": ws}
    finally:
        conn_cm.__exit__(None, None, None)
