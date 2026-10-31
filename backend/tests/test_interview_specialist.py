"""Harness-contract tests for the orchestrator-facing Interview Agent adapter."""

from app.interview_specialist import InterviewAssignment, run_interview_assignment


def assignment(outcome="conduct", mode="quick_screening", session_id=1, **context_overrides):
    context = {
        "candidate_ref": "candidate-1",
        "role_ref": "role-1",
        "role_version": "v2",
        "mode": mode,
        "session_id": session_id,
        "source_refs": [
            {"id": "company-1", "kind": "company", "content_hash": "sha256:company"},
            {"id": "cv-1", "kind": "cv", "content_hash": "sha256:abc"},
            {"id": "jd-1", "kind": "job_description", "content_hash": "sha256:def"},
        ],
        "criteria": [{"id": "current-role", "description": "Recent role and responsibilities"}],
    }
    context.update(context_overrides)
    return InterviewAssignment(
        request_ref="run-1/task-interview",
        requested_outcome=outcome,
        context=context,
    )


def session(state="running", report=None):
    return {
        "id": 1,
        "state": state,
        "error": None,
        "turns": [{"id": "answer-1", "answer": "private raw answer"}],
        "report": report,
    }


def test_running_session_needs_input_and_returns_refs_not_raw_answers():
    handoff = run_interview_assignment(assignment(), lambda _: session())
    body = handoff.model_dump()
    assert body["result"] == "Needs input"
    assert body["output"]["session_state"] == "running"
    assert body["output"]["submitted_answer_refs"] == ["interview-session:1#answer:answer-1"]
    assert "private raw answer" not in str(body)
    assert body["next_action"]["owner"] == "candidate"
    assert body["context"]["source_refs"][1]["content_hash"] == "sha256:abc"


def test_completed_conduct_handoff_preserves_specialist_scope():
    handoff = run_interview_assignment(assignment(), lambda _: session("done"))
    assert handoff.result == "Complete"
    assert handoff.assignment.agent == "Interview"
    assert handoff.output.artifact_refs == ["interview-session:1"]
    assert handoff.next_action is None


def test_reported_summary_returns_persisted_finding_references():
    report = {"findings": [{"criterion_id": "current-role", "statement": "Evidence."}]}
    handoff = run_interview_assignment(assignment("summarize"), lambda _: session("reported", report))
    assert handoff.result == "Complete"
    assert handoff.output.artifact_refs == ["interview-session:1", "interview-report:1"]
    assert handoff.output.finding_refs == ["interview-report:1#finding:current-role"]


def test_missing_context_is_a_needs_input_handoff_not_invented_context():
    handoff = run_interview_assignment(
        assignment(candidate_ref=None, role_version=None), lambda _: session("done")
    )
    assert handoff.result == "Needs input"
    assert handoff.output.artifact_refs == []
    assert handoff.open_items == ["Missing candidate_ref.", "Missing role_version."]
    assert handoff.next_action.owner == "orchestrator"


def test_cancelled_and_error_states_stay_visible():
    cancelled = run_interview_assignment(assignment(), lambda _: session("cancelled"))
    failed_session = session("error")
    failed_session["error"] = "provider unavailable"
    failed = run_interview_assignment(assignment(), lambda _: failed_session)
    assert (cancelled.result, cancelled.output.session_state) == ("Needs input", "cancelled")
    assert (failed.result, failed.output.session_state) == ("Failed", "error")
    assert failed.open_items == ["provider unavailable"]


def test_unpersisted_feature_modes_do_not_claim_completion():
    human = run_interview_assignment(
        assignment(mode="human_assistant", session_id=None), lambda _: None
    )
    prepare = run_interview_assignment(assignment("prepare"), lambda _: session("ready"))
    assert human.result == "Needs input"
    assert human.next_action.owner == "platform_team"
    assert prepare.result == "Needs input"
    assert prepare.output.unsaved_draft is False
