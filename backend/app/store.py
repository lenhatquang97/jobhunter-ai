"""Interview sessions in one SQLite table; JSON columns hold docs, index, transcript and report.

ponytail: SQLite file + one row per session is enough for a demo; move to Postgres when several backend processes
share sessions (the per-session lock below is process-local).
"""
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

DB = os.environ.get('INTERVIEW_DB', 'interviews.db')
JSON_COLS = ('docs', 'idx', 'pending', 'turns', 'flags', 'report')
_locks: dict[int, threading.Lock] = {}
_guard = threading.Lock()

def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS interview (
            id INTEGER PRIMARY KEY, token TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
            candidate TEXT NOT NULL, job TEXT NOT NULL, company TEXT NOT NULL,
            max_topics INTEGER NOT NULL, max_probes INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'ready', consent_at TEXT, error TEXT,
            docs TEXT, idx TEXT, pending TEXT, turns TEXT, flags TEXT, report TEXT)''')

def now():
    return datetime.now(timezone.utc).isoformat()

def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS:
        d[k] = json.loads(d[k]) if d[k] else None
    return d

def create(**vals):
    vals = dict(vals, token=secrets.token_urlsafe(32), created_at=now(), turns=[], flags=[])
    cols = list(vals)
    with _conn() as c:
        cur = c.execute(f"INSERT INTO interview ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [json.dumps(vals[k]) if k in JSON_COLS else vals[k] for k in cols])
        return get(cur.lastrowid)

def update(id_, **vals):
    with _conn() as c:
        c.execute(f"UPDATE interview SET {', '.join(f'{k}=?' for k in vals)} WHERE id=?",
                  [json.dumps(v) if k in JSON_COLS else v for k, v in vals.items()] + [id_])

def get(id_):
    with _conn() as c:
        return _row(c.execute('SELECT * FROM interview WHERE id=?', [id_]).fetchone())

def by_token(token):
    if not token or len(token) < 32:
        return None
    with _conn() as c:
        return _row(c.execute('SELECT * FROM interview WHERE token=?', [token]).fetchone())

def all_():
    with _conn() as c:
        return [_row(r) for r in c.execute('SELECT * FROM interview ORDER BY id DESC')]

class Busy(Exception):
    pass

@contextmanager
def turn_lock(id_):
    """One turn at a time per session: a double submit must not ask two questions."""
    with _guard:
        lock = _locks.setdefault(id_, threading.Lock())
    if not lock.acquire(blocking=False):
        raise Busy
    try:
        yield
    finally:
        lock.release()
