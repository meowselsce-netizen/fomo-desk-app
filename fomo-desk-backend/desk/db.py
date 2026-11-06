"""SQLite access: WAL, migrations, settings seed.

The paper runner keeps its database under ``var/paper/`` (gitignored), never
beside the static Pages snapshot. A context override lets tests and the
runner point at an isolated file without changing process-global imports.
"""
from __future__ import annotations

import contextvars
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from desk.settings_defaults import DEFAULTS

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "var" / "paper" / "desk.sqlite"
MIGRATIONS = Path(__file__).resolve().parent / "migrations"

_db_override: contextvars.ContextVar[Path | None] = contextvars.ContextVar("desk_db", default=None)
_run_override: contextvars.ContextVar[str | None] = contextvars.ContextVar("desk_run", default=None)


class PaperOnlyError(RuntimeError):
    """Raised when a LIVE command or a non-PAPER database would change state."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    override = _db_override.get()
    if override is not None:
        return Path(override)
    env = os.environ.get("FOMO_DESK_DB")
    if env:
        return Path(env)
    return DB_PATH


def current_run_id() -> str | None:
    return _run_override.get()


def use_db(path: Path | None) -> contextvars.Token:
    return _db_override.set(None if path is None else Path(path))


def use_run(run_id: str | None) -> contextvars.Token:
    return _run_override.set(run_id)


def reset_db(token: contextvars.Token) -> None:
    _db_override.reset(token)


def reset_run(token: contextvars.Token) -> None:
    _run_override.reset(token)


class isolated_db:
    """Bind connect() to one sqlite file and one paper run id."""

    def __init__(self, path: Path, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._db_tok: contextvars.Token | None = None
        self._run_tok: contextvars.Token | None = None

    def __enter__(self) -> "isolated_db":
        self._db_tok = use_db(self.path)
        self._run_tok = use_run(self.run_id)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._run_tok is not None:
            reset_run(self._run_tok)
        if self._db_tok is not None:
            reset_db(self._db_tok)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")
    return con


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def migrate(con: sqlite3.Connection | None = None) -> list[str]:
    own = con is None
    if own:
        con = connect()
    applied: list[str] = []
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
    # Drop Step-3 stub meta table if present (replaced by settings/balance)
    con.execute("DROP TABLE IF EXISTS meta")
    for path in _migration_files():
        ver = path.name
        if ver in done:
            continue
        sql = path.read_text()
        con.executescript(sql)
        con.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (ver, utc_now()),
        )
        applied.append(ver)
    seed_settings(con)
    con.commit()
    if own:
        con.close()
    return applied


def seed_settings(con: sqlite3.Connection, who: str = "migrate") -> None:
    now = utc_now()
    for key, value in DEFAULTS.items():
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )
            con.execute(
                "INSERT INTO change_log(ts, key, old, new, who) VALUES (?, ?, ?, ?, ?)",
                (now, key, None, value, who),
            )


def get_setting(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_setting(con: sqlite3.Connection, key: str, value: str, who: str = "system") -> None:
    if key == "mode" and str(value).strip().upper() != "PAPER":
        raise PaperOnlyError("mode changes other than PAPER are rejected")
    now = utc_now()
    old_row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    old = old_row["value"] if old_row else None
    con.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now),
    )
    con.execute(
        "INSERT INTO change_log(ts, key, old, new, who) VALUES (?, ?, ?, ?, ?)",
        (now, key, old, value, who),
    )


def all_settings(con: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}


def journal_mode(con: sqlite3.Connection) -> str:
    return con.execute("PRAGMA journal_mode").fetchone()[0]


def table_names(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def assert_paper_state(con: sqlite3.Connection) -> str:
    """Refuse to trade when the stored mode is anything but PAPER.

    Does not rewrite the row. A non-PAPER database is left untouched.
    """
    mode = (get_setting(con, "mode", "PAPER") or "PAPER").strip().upper()
    if mode != "PAPER":
        raise PaperOnlyError(f"database mode is {mode}; refusing to trade or rewrite it")
    return mode


def iter_settings(con: sqlite3.Connection) -> Iterator[tuple[str, str]]:
    for key, value in all_settings(con).items():
        yield key, value


def public_health_settings(con: sqlite3.Connection) -> dict[str, str]:
    """Settings safe to print. Omits nothing secret because secrets are not stored here."""
    allowed = set(DEFAULTS)
    return {k: v for k, v in all_settings(con).items() if k in allowed}
