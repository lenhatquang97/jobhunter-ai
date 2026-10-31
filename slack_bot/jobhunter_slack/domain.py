from datetime import UTC, datetime, time, timedelta
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
Language = Literal["en", "vi"]
TERMINAL = {"cancelled", "feedback_submitted", "not_happened", "expired"}


def utc(value: datetime) -> datetime:
    # SQLite drops offsets on DateTime fields. All stored timestamps are UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def work_opening(value: datetime) -> datetime:
    current = value.astimezone(ZONE)
    while current.weekday() >= 5 or current.time() >= time(18):
        current = datetime.combine(current.date() + timedelta(days=1), time(9), ZONE)
    if current.time() < time(9):
        current = datetime.combine(current.date(), time(9), ZONE)
    return current


def add_working_hours(value: datetime, hours: float) -> datetime:
    current = work_opening(value)
    remaining = timedelta(hours=hours)
    while remaining:
        closing = datetime.combine(current.date(), time(18), ZONE)
        available = closing - current
        if remaining <= available:
            return current + remaining
        remaining -= available
        current = work_opening(closing)
    return current


def next_working_day(value: datetime) -> datetime:
    local = value.astimezone(ZONE)
    return work_opening(datetime.combine(local.date() + timedelta(days=1), time(9), ZONE))


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ref: str = Field(min_length=1, max_length=120)
    lead_id: str = Field(pattern=r"^[UW][A-Z0-9]+$")
    starts_at: datetime
    ends_at: datetime
    meeting_url: str | None = Field(default=None, max_length=1000)
    feedback_url: str | None = Field(default=None, max_length=1000)

    @field_validator("candidate_ref")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Candidate reference is required")
        return value.strip()

    @field_validator("starts_at", "ends_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("An explicit timezone is required")
        if value.year >= 9999:
            raise ValueError("Date exceeds the supported scheduling range")
        try:
            return value.astimezone(UTC)
        except OverflowError:
            raise ValueError("Date cannot be represented in UTC") from None

    @field_validator("meeting_url", "feedback_url")
    @classmethod
    def safe_url(cls, value):
        from urllib.parse import urlsplit

        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or (parsed.username or parsed.password or any(c in value for c in "\r\n<>|"))
        ):
            raise ValueError("Use an http(s) link without embedded credentials")
        return value

    @model_validator(mode="after")
    def positive_duration(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("End must be after start")
        return self


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["propose", "cancel", "status", "help"]
    language: Language
    interview_id: str | None
    candidate_ref: str | None
    lead_id: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    meeting_url: str | None
    feedback_url: str | None
    question: str | None = Field(max_length=500)

    def proposal_data(self) -> dict:
        return self.model_dump(
            mode="json",
            exclude={
                "kind",
                "language",
                "interview_id",
                "question",
            },
            exclude_none=True,
        )
