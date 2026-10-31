"""Offline state-machine tests for the Orchestrator-facing Schedule Agent."""

from datetime import datetime, timezone

from app import schedule_agent, schedule_store
from app.schedule_provider import AvailabilitySnapshot, CandidateSnapshot


class FakeProvider:
    def get_candidate(self, candidate_ref: str) -> CandidateSnapshot:
        return CandidateSnapshot(
            candidate_ref=candidate_ref,
            job_ref="job-1",
            candidate_name=f"Candidate {candidate_ref}",
            candidate_email=f"candidate-{candidate_ref}@example.com",
            job_title="Backend Developer",
            stage="screening",
        )

    def check_interviewer_free(self, interviewer_ref, start, end):
        return AvailabilitySnapshot(
            interviewer_ref=interviewer_ref, start=start, end=end, available=True,
            internal_busy_blocks=0, google_busy_blocks=0,
        )


def assignment(outcome="invite_phone_screen", authorized=True, mode="dry_run", live=False):
    return schedule_agent.ScheduleAssignment(
        request_ref="run-1/schedule-phone",
        requested_outcome=outcome,
        context={
            "candidate_refs": ["2", "3"],
            "authorized_for_scheduling": authorized,
            "scheduled_for": "2030-01-20T09:00:00+07:00",
            "duration_minutes": 30,
            "timezone": "Asia/Ho_Chi_Minh",
            "response_deadline": "2030-01-18T17:00:00+07:00",
            "delivery_mode": mode,
            "interviewer_ref": "2" if live else None,
            "require_live_availability": live,
            "prior_output_refs": ["filter-run:1#candidate:2", "filter-run:1#candidate:3"],
        },
    )


def setup_store(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "DB", str(tmp_path / "schedule.db"))
    schedule_store.init()


def test_dry_run_delivery_is_idempotent_and_waits_without_holding_a_process(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    first = schedule_agent.run_schedule_assignment(assignment(), FakeProvider())
    second = schedule_agent.run_schedule_assignment(assignment(), FakeProvider())
    assert first.result == second.result == "Needs input"
    assert [item.state for item in first.output] == ["awaiting_response", "awaiting_response"]
    assert schedule_store.outbox_count() == 2
    assert first.next_action.owner == "candidate"


def test_all_acceptances_complete_the_assignment(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    schedule_agent.run_schedule_assignment(assignment(), FakeProvider())
    for invitation in schedule_store.by_request("run-1/schedule-phone"):
        schedule_store.respond(invitation["token"], "accepted")
    handoff = schedule_agent.run_schedule_assignment(assignment("status"), FakeProvider())
    assert handoff.result == "Complete"
    assert {item.state for item in handoff.output} == {"accepted"}
    assert handoff.next_action is None


def test_decline_and_ghost_are_business_outcomes_not_agent_failures(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    schedule_agent.run_schedule_assignment(assignment(), FakeProvider())
    invitations = schedule_store.by_request("run-1/schedule-phone")
    schedule_store.respond(invitations[0]["token"], "declined")
    schedule_store.expire_due("run-1/schedule-phone", datetime(2031, 1, 1, tzinfo=timezone.utc))
    handoff = schedule_agent.run_schedule_assignment(assignment("status"), FakeProvider())
    assert handoff.result == "Needs input"
    assert {item.state for item in handoff.output} == {"declined", "ghosted"}
    assert handoff.next_action.owner == "recruiter"


def test_missing_authorization_creates_no_invitation(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    handoff = schedule_agent.run_schedule_assignment(assignment(authorized=False), FakeProvider())
    assert handoff.result == "Needs input"
    assert schedule_store.by_request("run-1/schedule-phone") == []
    assert "authorization" in handoff.open_items[0].lower()


def test_mailpit_mode_refuses_non_local_smtp(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    handoff = schedule_agent.run_schedule_assignment(assignment(mode="mailpit"), FakeProvider())
    assert handoff.result == "Failed"
    assert {item.state for item in handoff.output} == {"delivery_failed"}


def test_response_after_deadline_becomes_ghosted(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    schedule_agent.run_schedule_assignment(assignment(), FakeProvider())
    invitation = schedule_store.by_request("run-1/schedule-phone")[0]
    monkeypatch.setattr(schedule_store, "now", lambda: datetime(2031, 1, 1, tzinfo=timezone.utc))
    result = schedule_store.respond(invitation["token"], "accepted")
    assert result["state"] == "ghosted"


def test_busy_live_calendar_prevents_invitation_and_delivery(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)

    class BusyProvider(FakeProvider):
        def check_interviewer_free(self, interviewer_ref, start, end):
            return AvailabilitySnapshot(
                interviewer_ref=interviewer_ref, start=start, end=end, available=False,
                internal_busy_blocks=1, google_busy_blocks=0,
            )

    handoff = schedule_agent.run_schedule_assignment(assignment(live=True), BusyProvider())
    assert handoff.result == "Needs input"
    assert handoff.availability.status == "busy"
    assert schedule_store.by_request("run-1/schedule-phone") == []


def test_smtp_test_uses_only_configured_test_recipient_after_live_check(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self): pass
        def login(self, user, password): pass
        def send_message(self, message):
            sent.append(message)
            return {}

    for key, value in {
        "SCHEDULE_LIVE_EMAIL_ENABLED": "true", "SCHEDULE_TEST_RECIPIENT": "recruiter@test.local",
        "SMTP_HOST": "smtp.test.local", "SMTP_PORT": "587", "SMTP_USER": "user",
        "SMTP_PASSWORD": "secret", "MAIL_FROM": "hr@test.local",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(schedule_agent.smtplib, "SMTP", SMTP)

    handoff = schedule_agent.run_schedule_assignment(
        assignment(mode="smtp_test", live=True), FakeProvider()
    )
    assert handoff.result == "Needs input"
    assert handoff.availability.status == "available"
    assert {message["To"] for message in sent} == {"recruiter@test.local"}
    assert all("candidate-" not in message["To"] for message in sent)
