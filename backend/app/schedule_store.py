"""Durable local state for resumable Schedule Agent invitation assignments."""

import os
import secrets
import sqlite3
from datetime import datetime, timezone


DB = os.environ.get("SCHEDULE_DB", "schedule.db")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection


def init() -> None:
    with _conn() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedule_invitation (
                id INTEGER PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                request_ref TEXT NOT NULL,
                candidate_ref TEXT NOT NULL,
                job_ref TEXT NOT NULL,
                candidate_name TEXT NOT NULL,
                candidate_email TEXT NOT NULL,
                job_title TEXT NOT NULL,
                kind TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                timezone TEXT NOT NULL,
                response_deadline TEXT NOT NULL,
                delivery_mode TEXT NOT NULL,
                delivery_ref TEXT,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                responded_at TEXT,
                UNIQUE(request_ref, candidate_ref, kind)
            );
            CREATE TABLE IF NOT EXISTS schedule_outbox (
                id INTEGER PRIMARY KEY,
                invitation_id INTEGER NOT NULL UNIQUE,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(invitation_id) REFERENCES schedule_invitation(id)
            );
            """
        )


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def create_or_get(
    *,
    request_ref: str,
    candidate_ref: str,
    job_ref: str,
    candidate_name: str,
    candidate_email: str,
    job_title: str,
    scheduled_for: datetime,
    duration_minutes: int,
    timezone_name: str,
    response_deadline: datetime,
    delivery_mode: str,
) -> tuple[dict, bool]:
    timestamp = _iso(now())
    with _conn() as connection:
        existing = connection.execute(
            "SELECT * FROM schedule_invitation WHERE request_ref=? AND candidate_ref=? AND kind='phone_screen'",
            (request_ref, candidate_ref),
        ).fetchone()
        if existing:
            return dict(existing), False
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO schedule_invitation (
                token, request_ref, candidate_ref, job_ref, candidate_name, candidate_email, job_title,
                kind, scheduled_for, duration_minutes, timezone, response_deadline, delivery_mode,
                state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'phone_screen', ?, ?, ?, ?, ?, 'prepared', ?, ?)
            """,
            (
                secrets.token_urlsafe(32), request_ref, candidate_ref, job_ref, candidate_name, candidate_email,
                job_title, _iso(scheduled_for), duration_minutes, timezone_name, _iso(response_deadline),
                delivery_mode, timestamp, timestamp,
            ),
        )
        created = cursor.rowcount == 1
        row = connection.execute(
            "SELECT * FROM schedule_invitation WHERE request_ref=? AND candidate_ref=? AND kind='phone_screen'",
            (request_ref, candidate_ref),
        ).fetchone()
        return dict(row), created


def update(invitation_id: int, **values) -> dict:
    values = dict(values, updated_at=_iso(now()))
    with _conn() as connection:
        connection.execute(
            f"UPDATE schedule_invitation SET {', '.join(f'{key}=?' for key in values)} WHERE id=?",
            [*values.values(), invitation_id],
        )
    return get(invitation_id)


def get(invitation_id: int) -> dict | None:
    with _conn() as connection:
        return _row(connection.execute("SELECT * FROM schedule_invitation WHERE id=?", (invitation_id,)).fetchone())


def by_token(token: str) -> dict | None:
    if len(token) < 32:
        return None
    with _conn() as connection:
        return _row(connection.execute("SELECT * FROM schedule_invitation WHERE token=?", (token,)).fetchone())


def by_request(request_ref: str) -> list[dict]:
    with _conn() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM schedule_invitation WHERE request_ref=? ORDER BY id", (request_ref,)
        )]


def add_outbox(invitation: dict, subject: str, body: str) -> str:
    with _conn() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO schedule_outbox (invitation_id, recipient, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (invitation["id"], invitation["candidate_email"], subject, body, _iso(now())),
        )
        outbox_id = cursor.lastrowid or connection.execute(
            "SELECT id FROM schedule_outbox WHERE invitation_id=?", (invitation["id"],)
        ).fetchone()[0]
    return f"schedule-outbox:{outbox_id}"


def outbox_count() -> int:
    with _conn() as connection:
        return connection.execute("SELECT count(*) FROM schedule_outbox").fetchone()[0]


def demo_outbox(request_ref: str) -> list[dict]:
    with _conn() as connection:
        return [dict(row) for row in connection.execute(
            """
            SELECT i.candidate_ref, i.state, i.token, o.subject, o.created_at
            FROM schedule_outbox o
            JOIN schedule_invitation i ON i.id = o.invitation_id
            WHERE i.request_ref=?
            ORDER BY o.id
            """,
            (request_ref,),
        )]


def respond(token: str, response: str) -> dict | None:
    invitation = by_token(token)
    if invitation is None or invitation["state"] != "awaiting_response":
        return invitation
    if datetime.fromisoformat(invitation["response_deadline"]) <= now():
        return update(invitation["id"], state="ghosted")
    state = "accepted" if response == "accepted" else "declined"
    return update(invitation["id"], state=state, responded_at=_iso(now()))


def expire_due(request_ref: str, at: datetime | None = None) -> list[dict]:
    current = at or now()
    for invitation in by_request(request_ref):
        deadline = datetime.fromisoformat(invitation["response_deadline"])
        if invitation["state"] == "awaiting_response" and deadline <= current:
            update(invitation["id"], state="ghosted")
    return by_request(request_ref)
