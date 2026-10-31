"""Read-only candidate lookup used by the Schedule Agent.

The remote Sea database remains the system of record. This adapter reads only
the minimum fields needed to address an invitation and never changes stages.
"""

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx


class ScheduleProviderError(Exception):
    pass


@dataclass(frozen=True)
class CandidateSnapshot:
    candidate_ref: str
    job_ref: str
    candidate_name: str
    candidate_email: str
    job_title: str
    stage: str


@dataclass(frozen=True)
class AvailabilitySnapshot:
    interviewer_ref: str
    start: datetime
    end: datetime
    available: bool
    internal_busy_blocks: int
    google_busy_blocks: int
    sources: tuple[str, ...] = ("sea_interview", "google_freebusy")


class SeaPostgresScheduleProvider:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL", "")

    def _url(self) -> str:
        url = self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        if not url:
            raise ScheduleProviderError("DATABASE_URL is not configured")
        return url

    def get_candidate(self, candidate_ref: str) -> CandidateSnapshot:
        try:
            candidate_id = int(candidate_ref)
        except ValueError as exc:
            raise ScheduleProviderError(f"invalid candidate_ref: {candidate_ref}") from exc
        try:
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                row = connection.execute(
                    """
                    SELECT a.id, a.job_id, a.name, a.email, a.stage, j.title
                    FROM applicant a JOIN job j ON j.id = a.job_id
                    WHERE a.id = %s AND NOT a.archived
                    """,
                    (candidate_id,),
                ).fetchone()
        except ScheduleProviderError:
            raise
        except Exception as exc:  # provider errors become explicit specialist failures
            raise ScheduleProviderError(f"Sea candidate lookup failed: {type(exc).__name__}") from exc
        if row is None:
            raise ScheduleProviderError(f"candidate {candidate_ref} was not found")
        return CandidateSnapshot(
            candidate_ref=str(row[0]),
            job_ref=str(row[1]),
            candidate_name=row[2],
            candidate_email=row[3],
            stage=str(row[4]),
            job_title=row[5],
        )

    def check_interviewer_free(
        self, interviewer_ref: str, start: datetime, end: datetime
    ) -> AvailabilitySnapshot:
        """Recheck both persisted Sea interviews and Google FreeBusy without exposing event details."""
        try:
            interviewer_id = int(interviewer_ref)
        except ValueError as exc:
            raise ScheduleProviderError(f"invalid interviewer_ref: {interviewer_ref}") from exc
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ScheduleProviderError("availability interval must be timezone-aware and increasing")
        try:
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                row = connection.execute(
                    """
                    SELECT u.email, g.refresh_token
                    FROM app_user u
                    LEFT JOIN google_credential g ON g.user_id = u.id
                    WHERE u.id = %s AND u.is_active
                    """,
                    (interviewer_id,),
                ).fetchone()
                if row is None:
                    raise ScheduleProviderError(f"interviewer {interviewer_ref} was not found or is inactive")
                internal_busy = connection.execute(
                    """
                    SELECT count(*) FROM interview
                    WHERE lower(interviewer_email) = lower(%s)
                      AND status = 'scheduled' AND start < %s AND "end" > %s
                    """,
                    (row[0], end.astimezone(timezone.utc), start.astimezone(timezone.utc)),
                ).fetchone()[0]
                refresh_token = row[1]
        except ScheduleProviderError:
            raise
        except Exception as exc:
            raise ScheduleProviderError(f"Sea availability lookup failed: {type(exc).__name__}") from exc
        if not refresh_token:
            raise ScheduleProviderError(f"interviewer {interviewer_ref} has not connected Google Calendar")
        google_busy = self._google_busy(refresh_token, start, end)
        return AvailabilitySnapshot(
            interviewer_ref=str(interviewer_id), start=start, end=end,
            available=internal_busy == 0 and len(google_busy) == 0,
            internal_busy_blocks=internal_busy, google_busy_blocks=len(google_busy),
        )

    @staticmethod
    def _google_busy(refresh_token: str, start: datetime, end: datetime) -> list[dict]:
        required = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
        if not all(os.environ.get(key) for key in required):
            raise ScheduleProviderError("Google Calendar client credentials are not configured")
        try:
            auth = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "refresh_token": refresh_token,
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "grant_type": "refresh_token",
                },
                timeout=15,
            )
            auth.raise_for_status()
            response = httpx.post(
                "https://www.googleapis.com/calendar/v3/freeBusy",
                headers={"Authorization": f"Bearer {auth.json()['access_token']}"},
                json={
                    "timeMin": start.astimezone(timezone.utc).isoformat(),
                    "timeMax": end.astimezone(timezone.utc).isoformat(),
                    "items": [{"id": "primary"}],
                },
                timeout=15,
            )
            response.raise_for_status()
            return response.json().get("calendars", {}).get("primary", {}).get("busy", [])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 0
            detail = "insufficient Calendar scope; reconnect Google" if status == 403 else type(exc).__name__
            raise ScheduleProviderError(f"Google FreeBusy failed: {detail}") from exc

    def calendar_connect_url(self, interviewer_ref: str) -> str:
        secret = os.environ.get("SECRET_KEY", "")
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/schedule/google/callback")
        if not secret or not client_id:
            raise ScheduleProviderError("Google OAuth configuration is incomplete")
        try:
            interviewer_id = int(interviewer_ref)
        except ValueError as exc:
            raise ScheduleProviderError(f"invalid interviewer_ref: {interviewer_ref}") from exc
        payload = f"{interviewer_id}.{int(time.time()) + 600}"
        signature = hmac.new(secret.encode(), f"schedule-google|{payload}".encode(), hashlib.sha256).hexdigest()
        state = f"{payload}.{signature}"
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email https://www.googleapis.com/auth/calendar.freebusy",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        })

    def finish_calendar_connect(self, state: str, code: str) -> str:
        secret = os.environ.get("SECRET_KEY", "")
        try:
            interviewer_ref, expires, signature = state.split(".")
            payload = f"{interviewer_ref}.{expires}"
            expected = hmac.new(secret.encode(), f"schedule-google|{payload}".encode(), hashlib.sha256).hexdigest()
            valid = hmac.compare_digest(signature, expected) and int(expires) >= time.time()
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ScheduleProviderError("Google OAuth state is invalid or expired")
        try:
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": os.environ["GOOGLE_CLIENT_ID"],
                    "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                    "redirect_uri": os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/schedule/google/callback"),
                    "grant_type": "authorization_code",
                },
                timeout=15,
            )
            response.raise_for_status()
            refresh_token = response.json().get("refresh_token")
            if not refresh_token:
                raise ScheduleProviderError("Google returned no refresh token; reconnect with consent")
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute(
                    """
                    INSERT INTO google_credential (user_id, refresh_token, email, created_at)
                    VALUES (%s, %s, '', now())
                    ON CONFLICT (user_id) DO UPDATE SET refresh_token = EXCLUDED.refresh_token
                    """,
                    (int(interviewer_ref), refresh_token),
                )
            return interviewer_ref
        except ScheduleProviderError:
            raise
        except Exception as exc:
            raise ScheduleProviderError(f"Google OAuth completion failed: {type(exc).__name__}") from exc

    def list_demo_candidate_refs(self, limit: int = 3) -> list[str]:
        """Return deterministic non-archived screening fixtures without exposing PII."""
        limit = max(1, min(limit, 10))
        try:
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                rows = connection.execute(
                    """
                    SELECT id FROM applicant
                    WHERE stage = 'screening' AND NOT archived
                      AND lower(email) LIKE '%%@example.com'
                    ORDER BY id LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
        except Exception as exc:
            raise ScheduleProviderError(f"Sea demo candidate lookup failed: {type(exc).__name__}") from exc
        return [str(row[0]) for row in rows]
