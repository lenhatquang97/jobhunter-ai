"""Orchestrator-facing Schedule Agent for resumable phone-screen invitations."""

import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from . import schedule_store
from .schedule_provider import AvailabilitySnapshot, CandidateSnapshot, ScheduleProviderError


ScheduleState = Literal[
    "prepared", "awaiting_response", "accepted", "declined", "ghosted",
    "delivery_failed", "cancelled",
]
HandoffResult = Literal["Complete", "Needs input", "Failed"]


class CandidateProvider(Protocol):
    def get_candidate(self, candidate_ref: str) -> CandidateSnapshot: ...
    def check_interviewer_free(
        self, interviewer_ref: str, start: datetime, end: datetime
    ) -> AvailabilitySnapshot: ...


class ScheduleContext(BaseModel):
    candidate_refs: list[str] = Field(min_length=1, max_length=20)
    authorized_for_scheduling: bool = False
    scheduled_for: datetime
    duration_minutes: int = Field(default=30, ge=5, le=240)
    timezone: str = Field(default="Asia/Ho_Chi_Minh", min_length=1, max_length=100)
    response_deadline: datetime
    delivery_mode: Literal["dry_run", "mailpit", "smtp_test", "smtp_live_demo"] = "dry_run"
    recipient_override: str | None = Field(default=None, max_length=320)
    interviewer_ref: str | None = None
    require_live_availability: bool = False
    prior_output_refs: list[str] = Field(default_factory=list, max_length=100)


class ScheduleAssignment(BaseModel):
    request_ref: str = Field(min_length=1, max_length=200)
    agent: Literal["Schedule"] = "Schedule"
    requested_outcome: Literal["invite_phone_screen", "status"] = "invite_phone_screen"
    harness_version: str = Field(default="v1.0", min_length=1, max_length=20)
    context: ScheduleContext


class AssignmentReference(BaseModel):
    request_ref: str
    agent: Literal["Schedule"] = "Schedule"
    requested_outcome: Literal["invite_phone_screen", "status"]
    harness_version: str


class InvitationOutput(BaseModel):
    invitation_ref: str
    candidate_ref: str
    job_ref: str
    state: ScheduleState
    scheduled_for_local: str
    scheduled_for_utc: str
    timezone: str
    response_deadline_utc: str
    delivery_ref: str | None = None


class NextAction(BaseModel):
    action: str
    owner: Literal["orchestrator", "schedule_agent", "recruiter", "candidate", "platform_team"]


class AvailabilityOutput(BaseModel):
    interviewer_ref: str
    start_utc: str
    end_utc: str
    status: Literal["available", "busy"]
    internal_busy_blocks: int
    google_busy_blocks: int
    sources: list[str]


class ScheduleHandoff(BaseModel):
    assignment: AssignmentReference
    context: ScheduleContext
    result: HandoffResult
    summary: str
    output: list[InvitationOutput] = Field(default_factory=list)
    availability: AvailabilityOutput | None = None
    open_items: list[str] = Field(default_factory=list)
    next_action: NextAction | None = None


def _assignment_ref(assignment: ScheduleAssignment) -> AssignmentReference:
    return AssignmentReference(
        request_ref=assignment.request_ref,
        agent=assignment.agent,
        requested_outcome=assignment.requested_outcome,
        harness_version=assignment.harness_version,
    )


def _handoff(assignment: ScheduleAssignment, result: HandoffResult, summary: str, invitations=None,
             open_items=None, next_action=None, availability=None) -> ScheduleHandoff:
    return ScheduleHandoff(
        assignment=_assignment_ref(assignment), context=assignment.context, result=result, summary=summary,
        output=[invitation_output(item) for item in invitations or []], availability=availability,
        open_items=open_items or [], next_action=next_action,
    )


def invitation_output(invitation: dict) -> InvitationOutput:
    scheduled_utc = datetime.fromisoformat(invitation["scheduled_for"]).astimezone(timezone.utc)
    scheduled_local = scheduled_utc.astimezone(ZoneInfo(invitation["timezone"]))
    return InvitationOutput(
        invitation_ref=f"schedule-invitation:{invitation['id']}",
        candidate_ref=invitation["candidate_ref"],
        job_ref=invitation["job_ref"],
        state=invitation["state"],
        scheduled_for_local=scheduled_local.isoformat(),
        scheduled_for_utc=scheduled_utc.isoformat(),
        timezone=invitation["timezone"],
        response_deadline_utc=datetime.fromisoformat(invitation["response_deadline"]).astimezone(timezone.utc).isoformat(),
        delivery_ref=invitation.get("delivery_ref"),
    )


def _message(invitation: dict) -> tuple[str, str]:
    response_url = f"{os.environ.get('BACKEND_URL', 'http://localhost:8000').rstrip('/')}/r/schedule/{invitation['token']}"
    scheduled_local = datetime.fromisoformat(invitation["scheduled_for"]).astimezone(
        ZoneInfo(invitation["timezone"])
    )
    subject = f"Phone screening invitation — {invitation['job_title']}"
    body = (
        f"Hello {invitation['candidate_name']},\n\n"
        f"You are invited to a phone screening for {invitation['job_title']} at "
        f"{scheduled_local.isoformat()} ({invitation['timezone']}).\n"
        f"Please accept or decline before {invitation['response_deadline']}:\n{response_url}\n\n"
        "This invitation does not represent a hiring decision."
    )
    return subject, body


def _deliver(invitation: dict) -> dict:
    subject, body = _message(invitation)
    if invitation["delivery_mode"] == "dry_run":
        delivery_ref = schedule_store.add_outbox(invitation, subject, body)
    elif invitation["delivery_mode"] == "mailpit":
        host = os.environ.get("SMTP_HOST", "localhost")
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return schedule_store.update(invitation["id"], state="delivery_failed")
        message = EmailMessage()
        message["From"] = os.environ.get("MAIL_FROM", "hr@example.com")
        message["To"] = invitation["candidate_email"]
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "1025")), timeout=10) as smtp:
                smtp.send_message(message)
            delivery_ref = f"mailpit:{invitation['id']}"
        except (OSError, smtplib.SMTPException):
            return schedule_store.update(invitation["id"], state="delivery_failed")
    elif invitation["delivery_mode"] == "smtp_test":
        if os.environ.get("SCHEDULE_LIVE_EMAIL_ENABLED", "false").lower() != "true":
            return schedule_store.update(invitation["id"], state="delivery_failed")
        recipient = os.environ.get("SCHEDULE_TEST_RECIPIENT") or os.environ.get("RECRUITER_EMAIL")
        required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")
        if not recipient or not all(os.environ.get(key) for key in required):
            return schedule_store.update(invitation["id"], state="delivery_failed")
        message = EmailMessage()
        message["From"] = os.environ["MAIL_FROM"]
        message["To"] = recipient
        message["Subject"] = f"[SCHEDULE TEST candidate {invitation['candidate_ref']}] {subject}"
        message.set_content(
            "Authorized integration-test delivery. The configured test inbox replaces the candidate address.\n\n" + body
        )
        try:
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=20) as smtp:
                if os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
                    smtp.starttls()
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                refused = smtp.send_message(message)
                if refused:
                    return schedule_store.update(invitation["id"], state="delivery_failed")
            delivery_ref = f"smtp-test:{invitation['id']}"
        except (OSError, smtplib.SMTPException, ValueError):
            return schedule_store.update(invitation["id"], state="delivery_failed")
    else:
        if os.environ.get("SCHEDULE_LIVE_EMAIL_ENABLED", "false").lower() != "true":
            return schedule_store.update(invitation["id"], state="delivery_failed")
        required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")
        if not all(os.environ.get(key) for key in required):
            return schedule_store.update(invitation["id"], state="delivery_failed")
        message = EmailMessage()
        message["From"] = os.environ["MAIL_FROM"]
        message["To"] = invitation["candidate_email"]
        message["Subject"] = f"[SCHEDULE DEMO] {subject}"
        message.set_content(body)
        try:
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=20) as smtp:
                if os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
                    smtp.starttls()
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                if smtp.send_message(message):
                    return schedule_store.update(invitation["id"], state="delivery_failed")
            delivery_ref = f"smtp-live-demo:{invitation['id']}"
        except (OSError, smtplib.SMTPException, ValueError):
            return schedule_store.update(invitation["id"], state="delivery_failed")
    return schedule_store.update(
        invitation["id"], state="awaiting_response", delivery_ref=delivery_ref
    )


def _validate(assignment: ScheduleAssignment) -> list[str]:
    context = assignment.context
    errors = []
    if assignment.harness_version != "v1.0":
        errors.append(f"Unsupported harness version: {assignment.harness_version}.")
    if not context.authorized_for_scheduling:
        errors.append("Scheduling authorization is missing.")
    try:
        ZoneInfo(context.timezone)
    except ZoneInfoNotFoundError:
        errors.append(f"Unknown IANA timezone: {context.timezone}.")
    if context.scheduled_for.tzinfo is None or context.response_deadline.tzinfo is None:
        errors.append("scheduled_for and response_deadline must include UTC offsets.")
    elif context.response_deadline >= context.scheduled_for:
        errors.append("response_deadline must be before scheduled_for.")
    if len(context.candidate_refs) != len(set(context.candidate_refs)):
        errors.append("candidate_refs contains duplicates.")
    if context.require_live_availability and not context.interviewer_ref:
        errors.append("interviewer_ref is required for live availability checking.")
    if context.delivery_mode == "smtp_test" and not context.require_live_availability:
        errors.append("smtp_test requires a successful live availability check.")
    if context.delivery_mode == "smtp_live_demo" and not context.recipient_override:
        errors.append("recipient_override is required for a real demo email.")
    return errors


def _availability_output(snapshot: AvailabilitySnapshot) -> AvailabilityOutput:
    return AvailabilityOutput(
        interviewer_ref=snapshot.interviewer_ref,
        start_utc=snapshot.start.astimezone(timezone.utc).isoformat(),
        end_utc=snapshot.end.astimezone(timezone.utc).isoformat(),
        status="available" if snapshot.available else "busy",
        internal_busy_blocks=snapshot.internal_busy_blocks,
        google_busy_blocks=snapshot.google_busy_blocks,
        sources=list(snapshot.sources),
    )


def _summarize(assignment: ScheduleAssignment, invitations: list[dict]) -> ScheduleHandoff:
    states = {item["state"] for item in invitations}
    if not invitations:
        return _handoff(
            assignment, "Needs input", "No scheduling invitations exist for this assignment.",
            next_action=NextAction(action="Create the authorized invitations first.", owner="orchestrator"),
        )
    if "delivery_failed" in states:
        return _handoff(
            assignment, "Failed", "At least one invitation could not be delivered.", invitations,
            ["Fix the authorized delivery capability and retry only failed invitations."],
            NextAction(action="Repair delivery and retry the failed invitation.", owner="schedule_agent"),
        )
    if states == {"accepted"}:
        return _handoff(
            assignment, "Complete", "All candidates accepted the phone-screen invitation.", invitations,
        )
    if states & {"declined", "ghosted", "cancelled"}:
        return _handoff(
            assignment, "Needs input", "At least one candidate declined, timed out, or was cancelled.", invitations,
            ["A business scheduling outcome needs an Orchestrator or recruiter decision; it is not an agent failure."],
            NextAction(action="Choose whether to retry, offer another date, or stop scheduling.", owner="recruiter"),
        )
    return _handoff(
        assignment, "Needs input", "Invitations were delivered and are awaiting candidate responses.", invitations,
        ["The run is paused; no process is held open while waiting."],
        NextAction(action="Resume on candidate response or response deadline.", owner="candidate"),
    )


def run_schedule_assignment(assignment: ScheduleAssignment, provider: CandidateProvider) -> ScheduleHandoff:
    """Create or inspect idempotent phone-screen invitations for an Orchestrator assignment."""
    errors = _validate(assignment)
    if errors:
        return _handoff(
            assignment, "Needs input", "The scheduling assignment is not ready to run.", open_items=errors,
            next_action=NextAction(action="Correct the bounded assignment and retry.", owner="orchestrator"),
        )
    if assignment.requested_outcome == "status":
        invitations = schedule_store.expire_due(assignment.request_ref)
        return _summarize(assignment, invitations)
    availability = None
    if assignment.context.require_live_availability:
        try:
            start = assignment.context.scheduled_for
            snapshot = provider.check_interviewer_free(
                assignment.context.interviewer_ref or "",
                start,
                start + timedelta(minutes=assignment.context.duration_minutes),
            )
            availability = _availability_output(snapshot)
        except ScheduleProviderError as exc:
            return _handoff(
                assignment, "Failed", "Live interviewer availability could not be verified.",
                open_items=[str(exc)],
                next_action=NextAction(action="Reconnect or repair the calendar provider and retry.", owner="recruiter"),
            )
        if not snapshot.available:
            return _handoff(
                assignment, "Needs input", "The requested interviewer is busy at the proposed time.",
                open_items=["Choose another proposed time; no invitation was created or sent."],
                next_action=NextAction(action="Select another slot and recheck availability.", owner="orchestrator"),
                availability=availability,
            )
    invitations = []
    try:
        for candidate_ref in assignment.context.candidate_refs:
            candidate = provider.get_candidate(candidate_ref)
            candidate_email = (
                assignment.context.recipient_override
                if assignment.context.delivery_mode == "smtp_live_demo"
                else candidate.candidate_email
            )
            invitation, created = schedule_store.create_or_get(
                request_ref=assignment.request_ref,
                candidate_ref=candidate.candidate_ref,
                job_ref=candidate.job_ref,
                candidate_name=candidate.candidate_name,
                candidate_email=candidate_email,
                job_title=candidate.job_title,
                scheduled_for=assignment.context.scheduled_for,
                duration_minutes=assignment.context.duration_minutes,
                timezone_name=assignment.context.timezone,
                response_deadline=assignment.context.response_deadline,
                delivery_mode=assignment.context.delivery_mode,
            )
            invitations.append(
                _deliver(invitation)
                if created or invitation["state"] in {"prepared", "delivery_failed"}
                else invitation
            )
    except ScheduleProviderError as exc:
        return _handoff(
            assignment, "Failed", "Candidate data could not be read from the authorized provider.", invitations,
            [str(exc)], NextAction(action="Restore provider access and retry.", owner="schedule_agent"),
        )
    handoff = _summarize(assignment, invitations)
    handoff.availability = availability
    return handoff
