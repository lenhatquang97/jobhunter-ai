"""HTTP boundary tests for Schedule assignment and candidate response routes."""

from fastapi.testclient import TestClient

from app import schedule_store
from app import standalone as main
from app.schedule_provider import CandidateSnapshot


class FakeProvider:
    def get_candidate(self, candidate_ref: str) -> CandidateSnapshot:
        return CandidateSnapshot(
            candidate_ref=candidate_ref,
            job_ref="job-1",
            candidate_name="Candidate Demo",
            candidate_email="candidate@example.com",
            job_title="Backend Developer",
            stage="screening",
        )

    def list_demo_candidate_refs(self, limit=3):
        return ["2", "3", "10"][:limit]


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_store, "DB", str(tmp_path / "schedule.db"))
    monkeypatch.setattr(main, "SeaPostgresScheduleProvider", FakeProvider)
    monkeypatch.setenv("RECRUITER_TOKEN", "test-recruiter")
    schedule_store.init()
    return TestClient(main.app)


def payload():
    return {
        "request_ref": "run-api/schedule",
        "requested_outcome": "invite_phone_screen",
        "context": {
            "candidate_refs": ["2"],
            "authorized_for_scheduling": True,
            "scheduled_for": "2030-01-20T09:00:00+07:00",
            "duration_minutes": 30,
            "timezone": "Asia/Ho_Chi_Minh",
            "response_deadline": "2030-01-18T17:00:00+07:00",
            "delivery_mode": "dry_run",
        },
    }


def test_orchestrator_dispatch_then_candidate_acceptance(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    created = api.post(
        "/api/agents/schedule",
        headers={"Authorization": "Bearer test-recruiter"},
        json=payload(),
    )
    assert created.status_code == 200
    assert created.json()["result"] == "Needs input"
    invitation = schedule_store.by_request("run-api/schedule")[0]
    response = api.post(
        f"/api/schedule/respond/{invitation['token']}", json={"response": "accepted"}
    )
    assert response.status_code == 200
    status_payload = payload()
    status_payload["requested_outcome"] = "status"
    status = api.post(
        "/api/agents/schedule",
        headers={"Authorization": "Bearer test-recruiter"},
        json=status_payload,
    )
    assert status.json()["result"] == "Complete"
    assert status.json()["output"][0]["state"] == "accepted"


def test_demo_candidate_endpoint_exposes_refs_only(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    response = api.get(
        "/api/agents/schedule/demo-candidates?limit=3",
        headers={"Authorization": "Bearer test-recruiter"},
    )
    assert response.json() == {"candidate_refs": ["2", "3", "10"]}
    assert "email" not in response.text.lower()


def test_dry_run_outbox_exposes_response_link_without_recipient_pii(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    api.post(
        "/api/agents/schedule",
        headers={"Authorization": "Bearer test-recruiter"},
        json=payload(),
    )
    response = api.get(
        "/api/agents/schedule/outbox?request_ref=run-api%2Fschedule",
        headers={"Authorization": "Bearer test-recruiter"},
    )
    assert response.status_code == 200
    assert response.json()[0]["candidate_ref"] == "2"
    assert response.json()[0]["response_url"].startswith("http://localhost:8000/r/schedule/")
    assert "candidate@example.com" not in response.text
