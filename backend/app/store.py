from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


def ident():
    return str(uuid4())


def iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    owner_key: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(100), default="New chat")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    def public(self):
        return {
            "id": self.id,
            "title": self.title,
            "archived": self.archived,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_message_id"),
        Index(
            "one_active_run_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
            sqlite_where=text("status IN ('queued','running')"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    client_message_id: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSON)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    draft_text: Mapped[str] = mapped_column(Text, default="")
    draft_agent: Mapped[str] = mapped_column(String(32), default="Orchestrator")
    source_state: Mapped[str] = mapped_column(String(20), default="current")
    retry_of_run_id: Mapped[str | None] = mapped_column(String(36))

    def public(self, source_state=None):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "error": self.error,
            "source_state": source_state or self.source_state,
            "retry_of_run_id": self.retry_of_run_id,
        }


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    context_refs: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)

    def public(self, state="current"):
        hidden = self.role == "assistant" and state == "unavailable"
        return {
            "id": self.id,
            "role": self.role,
            "text": "" if hidden else self.text,
            "run_id": self.run_id,
            "created_at": iso(self.created_at),
            "context_refs": [] if hidden else self.context_refs,
            "sources": [] if hidden else self.sources,
            "visibility": "visible" if self.role == "user" or state == "current" else state,
        }


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    agent_id: Mapped[str | None] = mapped_column(String(32))
    parent_id: Mapped[str | None] = mapped_column(String(128))
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)

    def public(self, hidden=False):
        # Retain sequence/type so masking never breaks event cursors or activity settlement.
        sensitive = self.type.startswith(("tool.", "message.", "workflow."))
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "type": self.type,
            "timestamp": iso(self.timestamp),
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
            "tool_call_id": self.tool_call_id,
            "payload": {"visibility": "unavailable"} if hidden and sensitive else self.payload,
        }


class ConversationCandidate(Base):
    """Additive v2 table: existing v1 chat rows need no ALTER or reset."""

    __tablename__ = "conversation_candidates"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    applicant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer)
    focused_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class RunWorkflow(Base):
    __tablename__ = "run_workflows"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    request: Mapped[dict] = mapped_column(JSON)


class Store:
    def __init__(self, url):
        self.engine = create_async_engine(url, echo=False, hide_parameters=True)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self):
        await self.engine.dispose()
