"""Read-only candidate lookup used by the Schedule Agent.

The remote Sea database remains the system of record. This adapter reads only
the minimum fields needed to address an invitation and never changes stages.
"""

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

    def list_schedule_candidates(self, limit: int = 20) -> list[dict]:
        """Return recruiter-visible demo candidates with their assigned job interviewers."""
        limit = max(1, min(limit, 50))
        try:
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                rows = connection.execute(
                    """
                    SELECT a.id, a.name, a.email, j.id, j.title,
                           COALESCE(j.interview_duration_minutes, 30)
                    FROM applicant a JOIN job j ON j.id = a.job_id
                    WHERE NOT a.archived AND j.is_open
                    ORDER BY a.id LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
                output = []
                for row in rows:
                    interviewers = connection.execute(
                        """
                        SELECT u.id, u.name, u.email, (g.refresh_token IS NOT NULL)
                        FROM job_interviewer ji
                        JOIN app_user u ON u.id = ji.user_id AND u.is_active
                        LEFT JOIN google_credential g ON g.user_id = u.id
                        WHERE ji.job_id = %s ORDER BY u.id
                        """,
                        (row[3],),
                    ).fetchall()
                    output.append({
                        "candidate_ref": str(row[0]), "candidate_name": row[1],
                        "candidate_email": row[2], "job_ref": str(row[3]),
                        "job_title": row[4], "duration_minutes": row[5],
                        "interviewers": [
                            {"ref": str(item[0]), "name": item[1], "email": item[2],
                             "google_connected": bool(item[3])}
                            for item in interviewers
                        ],
                    })
                return output
        except Exception as exc:
            raise ScheduleProviderError(f"Schedule candidate lookup failed: {type(exc).__name__}") from exc

    def find_earliest_internal_slot(
        self, candidate_ref: str, *, days: int = 14, timezone_name: str = "Asia/Ho_Chi_Minh"
    ) -> dict:
        """Find the first shared slot against persisted interviews for all assigned interviewers."""
        from zoneinfo import ZoneInfo

        candidates = [item for item in self.list_schedule_candidates(50)
                      if item["candidate_ref"] == candidate_ref]
        if not candidates:
            raise ScheduleProviderError(f"candidate {candidate_ref} was not found")
        candidate = candidates[0]
        interviewers = candidate["interviewers"]
        if not interviewers:
            raise ScheduleProviderError("the candidate's job has no assigned interviewers")
        zone = ZoneInfo(timezone_name)
        now = datetime.now(zone)
        start_day = now.date() + timedelta(days=1)
        duration = timedelta(minutes=int(candidate["duration_minutes"] or 30))
        try:
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                for offset in range(days):
                    day = start_day + timedelta(days=offset)
                    if day.weekday() >= 5:
                        continue
                    slot = datetime.combine(day, datetime.min.time(), zone) + timedelta(hours=9)
                    end_of_day = slot.replace(hour=17)
                    while slot + duration <= end_of_day:
                        end = slot + duration
                        busy = connection.execute(
                            """
                            SELECT count(*) FROM interview
                            WHERE lower(interviewer_email) = ANY(%s)
                              AND status = 'scheduled' AND start < %s AND "end" > %s
                            """,
                            ([item["email"].lower() for item in interviewers],
                             end.astimezone(timezone.utc), slot.astimezone(timezone.utc)),
                        ).fetchone()[0]
                        google_verified = all(i["google_connected"] for i in interviewers)
                        google_free = True
                        if busy == 0 and google_verified:
                            google_free = all(
                                self.check_interviewer_free(item["ref"], slot, end).available
                                for item in interviewers
                            )
                        if busy == 0 and google_free:
                            return {
                                "candidate": candidate,
                                "scheduled_for": slot.isoformat(),
                                "duration_minutes": int(duration.total_seconds() / 60),
                                "timezone": timezone_name,
                                "response_deadline": (slot - timedelta(hours=16)).isoformat(),
                                "availability_source": "persisted interviews",
                                "google_verified": google_verified,
                            }
                        slot += timedelta(minutes=30)
        except ScheduleProviderError:
            raise
        except Exception as exc:
            raise ScheduleProviderError(f"Availability scan failed: {type(exc).__name__}") from exc
        raise ScheduleProviderError(f"no shared slot found in the next {days} days")

    def list_schedule_jobs(self, limit: int = 20) -> list[dict]:
        """Group recruiter-visible candidates and assigned interviewers by open job."""
        candidates = self.list_schedule_candidates(50)
        jobs: dict[str, dict] = {}
        for candidate in candidates:
            job = jobs.setdefault(candidate["job_ref"], {
                "job_ref": candidate["job_ref"], "job_title": candidate["job_title"],
                "duration_minutes": candidate["duration_minutes"],
                "interviewers": candidate["interviewers"], "candidates": [],
            })
            job["candidates"].append({
                "candidate_ref": candidate["candidate_ref"],
                "candidate_name": candidate["candidate_name"],
                "candidate_email": candidate["candidate_email"],
            })
        return list(jobs.values())[:max(1, min(limit, 50))]

    def plan_job_slots(
        self, job_ref: str, candidate_refs: list[str], *, days: int = 14,
        timezone_name: str = "Asia/Ho_Chi_Minh",
    ) -> dict:
        """Assign sequential, non-overlapping shared slots for selected candidates of one job."""
        from zoneinfo import ZoneInfo

        jobs = {item["job_ref"]: item for item in self.list_schedule_jobs(50)}
        job = jobs.get(job_ref)
        if not job:
            raise ScheduleProviderError(f"job {job_ref} was not found")
        known = {item["candidate_ref"]: item for item in job["candidates"]}
        if not candidate_refs or any(ref not in known for ref in candidate_refs):
            raise ScheduleProviderError("every selected candidate must belong to the selected job")
        interviewers = job["interviewers"]
        if not interviewers:
            raise ScheduleProviderError("the selected job has no assigned interviewers")
        zone = ZoneInfo(timezone_name)
        duration = timedelta(minutes=int(job["duration_minutes"] or 30))
        start_day = datetime.now(zone).date() + timedelta(days=1)
        slots: list[datetime] = []
        try:
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                for offset in range(days):
                    day = start_day + timedelta(days=offset)
                    if day.weekday() >= 5:
                        continue
                    slot = datetime.combine(day, datetime.min.time(), zone) + timedelta(hours=9)
                    end_of_day = slot.replace(hour=17)
                    while slot + duration <= end_of_day and len(slots) < len(candidate_refs):
                        end = slot + duration
                        busy = connection.execute(
                            """
                            SELECT count(*) FROM interview
                            WHERE lower(interviewer_email) = ANY(%s)
                              AND status = 'scheduled' AND start < %s AND "end" > %s
                            """,
                            ([item["email"].lower() for item in interviewers],
                             end.astimezone(timezone.utc), slot.astimezone(timezone.utc)),
                        ).fetchone()[0]
                        google_verified = all(item["google_connected"] for item in interviewers)
                        google_free = True
                        if busy == 0 and google_verified:
                            google_free = all(
                                self.check_interviewer_free(item["ref"], slot, end).available
                                for item in interviewers
                            )
                        overlaps_proposal = any(
                            slot < existing + duration and end > existing for existing in slots
                        )
                        if busy == 0 and google_free and not overlaps_proposal:
                            slots.append(slot)
                        slot += timedelta(minutes=30)
                    if len(slots) == len(candidate_refs):
                        break
        except ScheduleProviderError:
            raise
        except Exception as exc:
            raise ScheduleProviderError(f"Job availability scan failed: {type(exc).__name__}") from exc
        if len(slots) != len(candidate_refs):
            raise ScheduleProviderError(f"not enough shared slots found in the next {days} days")
        return {
            "job": job, "timezone": timezone_name,
            "google_verified": all(item["google_connected"] for item in interviewers),
            "availability_source": "persisted interviews",
            "busy_windows": self.job_busy_windows(job_ref, days=days, timezone_name=timezone_name),
            "proposals": [{
                "candidate": known[ref], "scheduled_for": slot.isoformat(),
                "response_deadline": (slot - timedelta(hours=16)).isoformat(),
                "duration_minutes": int(duration.total_seconds() / 60),
            } for ref, slot in zip(candidate_refs, slots, strict=True)],
        }

    def job_busy_windows(
        self, job_ref: str, *, days: int = 14, timezone_name: str = "Asia/Ho_Chi_Minh"
    ) -> list[dict]:
        """Expose only busy time ranges, never private calendar titles or descriptions."""
        from zoneinfo import ZoneInfo

        jobs = {item["job_ref"]: item for item in self.list_schedule_jobs(50)}
        job = jobs.get(job_ref)
        if not job:
            raise ScheduleProviderError(f"job {job_ref} was not found")
        zone = ZoneInfo(timezone_name)
        start = datetime.combine(datetime.now(zone).date() + timedelta(days=1), datetime.min.time(), zone)
        end = start + timedelta(days=days)
        interviewers = job["interviewers"]
        by_email = {item["email"].lower(): item for item in interviewers}
        windows: list[dict] = []
        try:
            import psycopg

            with psycopg.connect(self._url(), connect_timeout=8) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                rows = connection.execute(
                    """
                    SELECT interviewer_email, start, "end" FROM interview
                    WHERE lower(interviewer_email) = ANY(%s) AND status = 'scheduled'
                      AND start < %s AND "end" > %s ORDER BY start
                    """,
                    (list(by_email), end.astimezone(timezone.utc), start.astimezone(timezone.utc)),
                ).fetchall()
                for email, busy_start, busy_end in rows:
                    interviewer = by_email.get(email.lower())
                    if interviewer:
                        windows.append({
                            "interviewer_ref": interviewer["ref"], "interviewer_name": interviewer["name"],
                            "start": busy_start.astimezone(zone).isoformat(),
                            "end": busy_end.astimezone(zone).isoformat(), "source": "Internal calendar",
                        })
                credentials = connection.execute(
                    """
                    SELECT u.id, g.refresh_token FROM app_user u
                    JOIN google_credential g ON g.user_id = u.id
                    WHERE u.id = ANY(%s) AND g.refresh_token IS NOT NULL
                    """,
                    ([int(item["ref"]) for item in interviewers],),
                ).fetchall()
            by_ref = {item["ref"]: item for item in interviewers}
            for interviewer_id, refresh_token in credentials:
                interviewer = by_ref[str(interviewer_id)]
                for block in self._google_busy(refresh_token, start, end):
                    windows.append({
                        "interviewer_ref": str(interviewer_id),
                        "interviewer_name": interviewer["name"],
                        "start": block["start"], "end": block["end"], "source": "Google FreeBusy",
                    })
        except ScheduleProviderError:
            raise
        except Exception as exc:
            raise ScheduleProviderError(f"Busy-window lookup failed: {type(exc).__name__}") from exc
        return sorted(windows, key=lambda item: (item["start"], item["interviewer_ref"]))
