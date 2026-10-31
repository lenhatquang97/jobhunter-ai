"""Orchestrator-facing Interview Agent contract over the existing interview features.

This module does not start interviews or call models. It validates a bounded
assignment and turns persisted feature state into the shared harness handoff.
"""

from typing import Callable, Literal

from pydantic import BaseModel, Field


InterviewMode = Literal["quick_screening", "human_assistant"]
InterviewOutcome = Literal["prepare", "conduct", "summarize"]
HandoffResult = Literal["Complete", "Needs input", "Failed"]


class SourceReference(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    kind: Literal["company", "job_description", "cv", "transcript", "rubric", "other"] = "other"
    content_hash: str | None = Field(default=None, min_length=1, max_length=200)


class CriterionReference(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2_000)


class AssignmentContext(BaseModel):
    candidate_ref: str | None = Field(default=None, max_length=200)
    role_ref: str | None = Field(default=None, max_length=200)
    role_version: str | None = Field(default=None, max_length=100)
    mode: InterviewMode
    language: Literal["en", "vi"] = "en"
    session_id: int | None = Field(default=None, ge=1)
    source_refs: list[SourceReference] = Field(default_factory=list, max_length=100)
    prior_output_refs: list[str] = Field(default_factory=list, max_length=100)
    criteria: list[CriterionReference] = Field(default_factory=list, max_length=30)


class InterviewAssignment(BaseModel):
    request_ref: str = Field(min_length=1, max_length=200)
    agent: Literal["Interview"] = "Interview"
    requested_outcome: InterviewOutcome
    harness_version: str = Field(default="v1.0", min_length=1, max_length=20)
    context: AssignmentContext


class AssignmentReference(BaseModel):
    request_ref: str
    agent: Literal["Interview"] = "Interview"
    requested_outcome: InterviewOutcome
    harness_version: str


class NextAction(BaseModel):
    action: str
    owner: Literal["orchestrator", "interview_agent", "recruiter", "candidate", "platform_team"]


class InterviewOutput(BaseModel):
    session_state: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    submitted_answer_refs: list[str] = Field(default_factory=list)
    finding_refs: list[str] = Field(default_factory=list)
    unsaved_draft: bool = False


class InterviewHandoff(BaseModel):
    assignment: AssignmentReference
    context: AssignmentContext
    result: HandoffResult
    summary: str
    output: InterviewOutput
    open_items: list[str] = Field(default_factory=list)
    next_action: NextAction | None = None


def _handoff(
    assignment: InterviewAssignment,
    result: HandoffResult,
    summary: str,
    output: InterviewOutput | None = None,
    open_items: list[str] | None = None,
    next_action: NextAction | None = None,
) -> InterviewHandoff:
    return InterviewHandoff(
        assignment=AssignmentReference(
            request_ref=assignment.request_ref,
            agent=assignment.agent,
            requested_outcome=assignment.requested_outcome,
            harness_version=assignment.harness_version,
        ),
        context=assignment.context,
        result=result,
        summary=summary,
        output=output or InterviewOutput(),
        open_items=open_items or [],
        next_action=next_action,
    )


def _required_context(assignment: InterviewAssignment) -> list[str]:
    context = assignment.context
    missing = []
    if not context.candidate_ref:
        missing.append("candidate_ref")
    if not context.role_ref:
        missing.append("role_ref")
    if not context.role_version:
        missing.append("role_version")
    if not context.criteria:
        missing.append("approved criteria")
    if context.mode == "quick_screening":
        source_kinds = {source.kind for source in context.source_refs}
        for kind in ("company", "job_description", "cv"):
            if kind not in source_kinds:
                missing.append(f"{kind} source_ref")
    return missing


def _session_output(session: dict) -> InterviewOutput:
    session_id = session["id"]
    answer_refs = [
        f"interview-session:{session_id}#answer:{turn['id']}"
        for turn in session.get("turns") or []
        if turn.get("id")
    ]
    finding_refs = []
    if session.get("report"):
        finding_refs = [
            f"interview-report:{session_id}#finding:{finding['criterion_id']}"
            for finding in session["report"].get("findings", [])
            if finding.get("criterion_id")
        ]
    artifacts = [f"interview-session:{session_id}"]
    if session.get("report"):
        artifacts.append(f"interview-report:{session_id}")
    return InterviewOutput(
        session_state=session.get("state"),
        artifact_refs=artifacts,
        submitted_answer_refs=answer_refs,
        finding_refs=finding_refs,
    )


def run_interview_assignment(
    assignment: InterviewAssignment,
    get_session: Callable[[int], dict | None],
) -> InterviewHandoff:
    """Return a harness v1.0 specialist handoff without dispatching other agents."""
    if assignment.harness_version != "v1.0":
        return _handoff(
            assignment,
            "Failed",
            "The Interview Agent cannot run with an unsupported harness version.",
            open_items=[f"Unsupported harness version: {assignment.harness_version}"],
            next_action=NextAction(action="Re-plan with harness v1.0.", owner="orchestrator"),
        )

    missing = _required_context(assignment)
    if missing:
        return _handoff(
            assignment,
            "Needs input",
            "The orchestrator assignment is missing required interview context.",
            open_items=[f"Missing {name}." for name in missing],
            next_action=NextAction(
                action="Supply the missing candidate, approved role/criteria, and authorized source references.",
                owner="orchestrator",
            ),
        )

    context = assignment.context
    if context.mode == "human_assistant":
        return _handoff(
            assignment,
            "Needs input",
            "The human interview assistant is available as a feature but has no persisted specialist artifact yet.",
            open_items=[
                "Persist the attributed final transcript, rubric version, follow-ups, and feedback before claiming a handoff."
            ],
            next_action=NextAction(
                action="Add persisted human-assistant artifacts, then retry this bounded assignment.",
                owner="platform_team",
            ),
        )

    if assignment.requested_outcome == "prepare":
        return _handoff(
            assignment,
            "Needs input",
            "Question-guide preparation is not exposed as a persisted Interview Agent artifact yet.",
            open_items=["No verified prepared-guide reference exists."],
            next_action=NextAction(
                action="Add a persisted criterion-linked guide capability before retrying preparation.",
                owner="platform_team",
            ),
        )

    if context.session_id is None:
        return _handoff(
            assignment,
            "Needs input",
            "The assignment does not identify an interview session.",
            open_items=["Missing session_id."],
            next_action=NextAction(action="Supply the authorized interview session ID.", owner="orchestrator"),
        )

    session = get_session(context.session_id)
    if session is None:
        return _handoff(
            assignment,
            "Failed",
            "The referenced interview session does not exist.",
            open_items=[f"Session {context.session_id} was not found."],
            next_action=NextAction(action="Correct the session reference or create an authorized session.", owner="orchestrator"),
        )

    output = _session_output(session)
    state = session.get("state")
    if state == "error":
        return _handoff(
            assignment,
            "Failed",
            "The interview feature ended with an error.",
            output,
            [session.get("error") or "The persisted session has no error detail."],
            NextAction(action="Resolve the recorded error and retry only the affected work.", owner="interview_agent"),
        )
    if state == "cancelled":
        return _handoff(
            assignment,
            "Needs input",
            "The interview was cancelled; confirmed submitted answers remain preserved.",
            output,
            ["The requested interview outcome was not completed."],
            NextAction(action="A recruiter must decide whether a new interview is authorized.", owner="recruiter"),
        )

    if assignment.requested_outcome == "conduct":
        if state in {"done", "reported"}:
            return _handoff(
                assignment,
                "Complete",
                "The authorized quick screening finished and its submitted answers are persisted.",
                output,
            )
        return _handoff(
            assignment,
            "Needs input",
            "The quick screening is awaiting candidate participation.",
            output,
            [f"Session is {state}; unanswered work is not completed participation."],
            NextAction(action="Continue or finish the authorized candidate interview.", owner="candidate"),
        )

    if state == "reported" and session.get("report"):
        return _handoff(
            assignment,
            "Complete",
            "The evidence-grounded interview report is persisted and ready for orchestrator validation.",
            output,
        )
    if state == "done":
        return _handoff(
            assignment,
            "Needs input",
            "Candidate participation finished, but the persisted report is not ready.",
            output,
            ["Report generation is pending."],
            NextAction(action="Wait for or retry report generation.", owner="interview_agent"),
        )
    return _handoff(
        assignment,
        "Needs input",
        "The interview cannot be summarized before candidate participation finishes.",
        output,
        [f"Session is {state}."],
        NextAction(action="Complete the authorized interview before requesting a summary.", owner="candidate"),
    )
