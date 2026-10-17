"""SQLite storage. One file, created on first run. Every row has a timestamp: the room's record."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DEALROOM_DATA_DIR", ROOT / "data"))
UPLOADS = DATA_DIR / "uploads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS person (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, name TEXT, role TEXT, email TEXT);
CREATE TABLE IF NOT EXISTS room (
  id INTEGER PRIMARY KEY, title TEXT, freelancer_id INTEGER, client_id INTEGER,
  status TEXT DEFAULT 'talking', story TEXT, created TEXT, suggest TEXT, parent_id INTEGER);
CREATE TABLE IF NOT EXISTS message (
  id INTEGER PRIMARY KEY, room_id INTEGER, sender_id INTEGER,
  kind TEXT DEFAULT 'chat',          -- chat | note (AI or system, shared) | private (one person only)
  level TEXT,                        -- good | warn | bad | info
  only_for INTEGER,                  -- person id for private notes
  text TEXT, data TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS terms (
  id INTEGER PRIMARY KEY, room_id INTEGER, version INTEGER, data TEXT, source TEXT,
  accepted_freelancer TEXT, accepted_client TEXT, invoice_id TEXT, invoice_number TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS payment (
  id INTEGER PRIMARY KEY, room_id INTEGER, part TEXT, amount REAL, currency TEXT DEFAULT 'USD',
  order_id TEXT, capture_id TEXT, status TEXT, payer_email TEXT, ts TEXT, paid_ts TEXT);
CREATE TABLE IF NOT EXISTS delivery (
  id INTEGER PRIMARY KEY, room_id INTEGER, filename TEXT, stored TEXT, sha256 TEXT,
  check_data TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS download (
  id INTEGER PRIMARY KEY, delivery_id INTEGER, person_id INTEGER, ip TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS dispute (
  id INTEGER PRIMARY KEY, room_id INTEGER, paypal_id TEXT, reason TEXT, claim TEXT,
  status TEXT, report TEXT, sent_ts TEXT, ts TEXT);
"""

_locks: dict[str, threading.RLock] = {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or DATA_DIR / "dealroom.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for table, col in (("room", "suggest TEXT"), ("room", "parent_id INTEGER"), ("person", "seen_note INTEGER DEFAULT 0")):
        try:   # copies made before these columns existed
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    return conn


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()


def all_(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def insert(conn, table: str, **cols) -> int:
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    cur = conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})", tuple(cols.values()))
    conn.commit()
    return cur.lastrowid


def update(conn, table: str, row_id: int, **cols):
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*cols.values(), row_id))
    conn.commit()


def jload(s, default=None):
    try:
        return json.loads(s) if s else default
    except (TypeError, ValueError):
        return default
