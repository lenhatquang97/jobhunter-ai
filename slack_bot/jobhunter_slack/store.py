import asyncio
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Interview(Base):
    __tablename__ = "slack_interviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(32))
    lead_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(32), default="pending_confirmation")
    data: Mapped[dict] = mapped_column(JSON)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    feedback_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Draft(Base):
    __tablename__ = "slack_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="propose")
    target_id: Mapped[str | None] = mapped_column(String(32))
    expected_version: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    channel: Mapped[str] = mapped_column(String(32))
    thread_ts: Mapped[str] = mapped_column(String(32))
    question: Mapped[str | None] = mapped_column(String(500))


class Member(Base):
    __tablename__ = "slack_members"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    language: Mapped[str] = mapped_column(String(2), default="en")


class Thread(Base):
    __tablename__ = "slack_threads"
    __table_args__ = (UniqueConstraint("interview_id", "recipient"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    interview_id: Mapped[str] = mapped_column(String(32), index=True)
    recipient: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(32))
    thread_ts: Mapped[str] = mapped_column(String(32))


class Inbox(Base):
    __tablename__ = "slack_inbox"
    id: Mapped[str] = mapped_column(String(240), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "slack_notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    dedup_key: Mapped[str] = mapped_column(String(240), unique=True)
    recipient: Mapped[str] = mapped_column(String(32))
    interview_id: Mapped[str | None] = mapped_column(String(32), index=True)
    version: Mapped[int | None] = mapped_column(Integer)
    draft_id: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    slack_ts: Mapped[str | None] = mapped_column(String(32))


class Store:
    def __init__(self, url):
        if hasattr(url, "get_secret_value"):
            url = url.get_secret_value()
        self.engine = create_async_engine(url, echo=False, hide_parameters=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.lock = asyncio.Lock()

    async def initialize(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def recover(self):
        async with self.sessions.begin() as session:
            await session.execute(
                update(Inbox)
                .where(Inbox.status == "processing")
                .values(
                    status="pending",
                )
            )
            await session.execute(
                update(Notification)
                .where(
                    Notification.status == "sending",
                )
                .values(status="uncertain", error_code="interrupted_delivery")
            )

    async def close(self):
        await self.engine.dispose()
