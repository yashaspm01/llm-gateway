import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config

from gateway.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


def test_migrations_build_the_current_schema(tmp_path, monkeypatch):
    db = tmp_path / "migrated.db"
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        get_settings.cache_clear()

    with closing(sqlite3.connect(db)) as conn:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        team_columns = {r[1] for r in conn.execute("pragma table_info(teams)")}
    assert {"teams", "api_keys", "usage_records", "alembic_version"} <= tables
    assert {"rate_limit_per_minute", "daily_token_budget"} <= team_columns
