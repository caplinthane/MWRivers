"""
SQLite storage for Palmy River Watch.

Why a database at all? The Hilltop server only ever gives us whatever
window we ask for (e.g. the last 48 hours) — it's not a long-term archive
from this app's point of view. By polling periodically and storing what
we get, this app builds its own history over time, which unlocks things
the live API alone can't give us cheaply: multi-week trend charts,
day-by-day stats, and resilience if Horizons' server is briefly down
(we can still serve the last known data instead of erroring).

Schema is deliberately simple: one row per (site, timestamp) reading.
`INSERT OR IGNORE` on a composite primary key means re-polling overlapping
time windows is safe — duplicates are just silently skipped rather than
raising an error or creating a second copy.

Every function here opens its own short-lived connection via
get_connection() rather than sharing one long-lived connection — SQLite
handles this fine at our scale, and it avoids any thread-safety headaches
between FastAPI's request handlers and the background poller.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from hilltop_client import Reading

DB_PATH = Path(__file__).parent / "river_watch.db"


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

@contextmanager
def get_connection():
    """
    Open a SQLite connection as a context manager (`with get_connection()
    as conn:`), so it always gets closed even if the code inside raises.

    `row_factory = sqlite3.Row` makes query results behave like
    dictionaries (`row["value"]` instead of `row[1]`) instead of plain
    tuples — much easier to read, and lets us do `dict(row)` to turn a
    result straight into a JSON-serialisable dict.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create the readings table if it doesn't already exist. Safe to call
    every time the app starts — CREATE TABLE IF NOT EXISTS is a no-op on
    an existing database."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                site_key TEXT NOT NULL,
                time     TEXT NOT NULL,  -- ISO 8601
                value    REAL NOT NULL,
                unit     TEXT,
                PRIMARY KEY (site_key, time)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_site_time "
            "ON readings (site_key, time)"
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def store_readings(site_key: str, readings: Iterable[Reading], unit: str | None) -> int:
    """
    Insert new readings for a site, silently skipping any (site, time)
    pair already stored. Returns how many rows were actually new — the
    poller logs this so you can see at a glance whether a poll found
    fresh data or just re-fetched a window it already had.
    """
    rows = [(site_key, r.time.isoformat(), r.value, unit) for r in readings]
    if not rows:
        return 0
    with get_connection() as conn:
        cur = conn.executemany(
            "INSERT OR IGNORE INTO readings (site_key, time, value, unit) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# Reading — each of these backs one API endpoint in main.py
# ---------------------------------------------------------------------------

def get_history(site_key: str, since_iso: str) -> list[dict]:
    """Every stored reading for a site from `since_iso` onwards, oldest
    first. Backs GET /api/sites/{key}/data."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT time, value, unit FROM readings "
            "WHERE site_key = ? AND time >= ? ORDER BY time",
            (site_key, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats(site_key: str, since_iso: str) -> dict | None:
    """Min / max / avg / count across the whole period — one summary row,
    not a time series. Backs GET /api/sites/{key}/stats."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MIN(value) AS min, MAX(value) AS max, AVG(value) AS avg, "
            "COUNT(*) AS count FROM readings WHERE site_key = ? AND time >= ?",
            (site_key, since_iso),
        ).fetchone()
        return dict(row) if row else None


def get_daily_rollup(site_key: str, since_iso: str) -> list[dict]:
    """One row per calendar day: min/max/avg/count for that day. Backs
    GET /api/sites/{key}/daily, which powers the frontend's 30-day chart.

    `strftime('%Y-%m-%d', time)` truncates each timestamp down to just its
    date, and GROUP BY that expression buckets every reading from the same
    day together before MIN/MAX/AVG run on each bucket."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT strftime('%Y-%m-%d', time) AS date, "
            "MIN(value) AS min, MAX(value) AS max, AVG(value) AS avg, COUNT(*) AS count "
            "FROM readings WHERE site_key = ? AND time >= ? "
            "GROUP BY date ORDER BY date",
            (site_key, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]
