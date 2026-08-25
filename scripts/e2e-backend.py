"""Run the Playwright backend against a disposable PostgreSQL database."""

import os
import runpy
import signal
from pathlib import Path
from unittest.mock import patch

import psycopg
import uvicorn

from app import config, db
from app.services.api_keys import create_key

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def main() -> None:
    base = config.DATABASE_URL
    if not base:
        raise SystemExit("The database is not configured. Set SKEIN_DATABASE_URL first.")

    name = f"skein_e2e_{os.getpid()}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')

    # conninfo-aware, never string surgery: DATABASE_URL can be a keyword
    # conninfo composed from SKEIN_DB_* components, where rsplit("/") mangles
    # the dbname and truncates a password holding "/".
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    info = conninfo_to_dict(base)
    info["dbname"] = name
    url = make_conninfo(**{k: str(v) for k, v in info.items() if v is not None})
    os.environ["SKEIN_DATABASE_URL"] = url
    config.DATABASE_URL, config.DATABASE_ERROR = url, ""
    db.close_pool()

    # Uvicorn re-raises SIGTERM after shutdown. The default handler terminates
    # without unwinding this frame, so the disposable database would survive.
    def unwind_after_sigterm(_signum, _frame) -> None:
        raise SystemExit(0)

    previous_sigterm = signal.signal(signal.SIGTERM, unwind_after_sigterm)
    try:
        runpy.run_path(str(BACKEND / "seed.py"), run_name="__main__")
        # e2e/smoke.spec.ts sends this fixed test key. A random key leaves the
        # browser weak and the interactive crew controls never render.
        with patch("app.services.api_keys.secrets.token_hex", return_value="0" * 40):
            create_key("ava", "Playwright")
        with patch("app.services.api_keys.secrets.token_hex", return_value="1" * 40):
            create_key("marcus", "Playwright shared chat")
        uvicorn.run("app.main:app", host="127.0.0.1", port=8600)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        # WITH FORCE closes this process's test connections. Closing the pool
        # first can outlive Playwright's shutdown timeout and strand the database.
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


if __name__ == "__main__":
    main()
