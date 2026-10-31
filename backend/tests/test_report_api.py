from app import standalone
from fastapi.testclient import TestClient


def test_report_endpoint_returns_harness_handoff(monkeypatch):
    monkeypatch.setenv("RECRUITER_TOKEN", "report-token")
    response = TestClient(standalone.app).post(
        "/api/agents/report", headers={"Authorization": "Bearer report-token"}, json={
            "run_id": "run-http", "assignment": "Compile interview evidence",
            "candidate_id": "candidate-1", "role_id": "backend", "role_version": "v1",
            "branches": [{
                "name": "Interview", "status": "Needs input", "requested": True,
                "summary": "No submitted answers.", "findings": [],
                "evidence_gaps": [{"reason": "No submitted answers"}],
                "limitations": ["Prepared questions are not completed participation."],
                "open_items": ["Candidate participation is pending."],
                "next_action": {"action": "Wait for a submitted answer.", "owner": "candidate"},
                "artifact_refs": [{"id": "session-1", "status": "prepared"}],
            }],
        },
    )
    assert response.status_code == 200
    assert response.json()["report_status"] == "Complete"
    assert response.json()["overall_status"] == "Needs input"
    assert "score" not in response.text.lower()
