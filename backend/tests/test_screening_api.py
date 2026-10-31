"""API behavior tests for the guided screening workflow."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main, store


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "screening.db"))
    monkeypatch.setenv("RECRUITER_TOKEN", "test-recruiter")
    store.init()
    return TestClient(main.app)


def _create(client):
    with patch.object(main.agent, "build_index", return_value=[{
        "source": "cv", "text": "Candidate Python experience", "vec": [1.0]
    }]):
        response = client.post("/api/interviews", headers={"Authorization": "Bearer test-recruiter"}, data={
            "candidate": "Candidate One",
            "job": "Backend Engineer",
            "company": "Acme",
            "company_background": "Acme builds useful software products.",
            "job_description": "Build and maintain Python backend services.",
            "job_requirements": "Two years of Python and API development experience.",
            "cv_plain": "Candidate built Python APIs for several projects.",
            "max_topics": "1",
            "max_probes": "0",
        })
    assert response.status_code == 200
    created = response.json()
    stored = store.get(created["id"])
    assert "Requirements / must-haves" in stored["docs"]["job_description"]
    assert "Two years of Python" in stored["docs"]["job_description"]
    return created


def test_submitted_answer_gets_stable_evidence_id_and_language(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session = _create(client)
    opening = {"question": "Tell me about your current role.", "done": False, "action": "new_topic",
               "topic": "current role", "topic_no": 1, "evidence": "", "flags": []}
    closing = {"question": "Thank you.", "done": True, "action": "end", "topic": "closing",
               "topic_no": 2, "evidence": "Candidate described APIs.", "flags": []}
    report = {"summary": "Draft", "strengths": [], "gaps": [], "cv_claims": [], "findings": []}
    with patch.object(main.agent, "next_turn", side_effect=[opening, closing]), \
         patch.object(main.agent, "report", return_value=report):
        assert client.post(f"/api/i/{session['url'].split('/')[-1]}/start").status_code == 200
        answer = client.post(f"/api/i/{session['url'].split('/')[-1]}/answer",
                             json={"text": "Tôi xây dựng API bằng Python.", "language": "vi"})
    assert answer.status_code == 200
    detail = client.get(f"/api/interviews/{session['id']}",
                        headers={"Authorization": "Bearer test-recruiter"}).json()
    assert detail["turns"][0]["id"] == "answer-1"
    assert detail["turns"][0]["language"] == "vi"
    assert detail["turns"][0]["submitted_at"]


def test_audio_consent_can_be_granted_and_revoked_without_starting(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session = _create(client)
    token = session["url"].split("/")[-1]
    assert client.post(f"/api/i/{token}/consent", json={"consent": True}).json()["audio_consent"] is True
    assert client.get(f"/api/i/{token}").json()["audio_consent"] is True
    assert client.post(f"/api/i/{token}/consent", json={"consent": False}).json()["audio_consent"] is False
    assert client.get(f"/api/i/{token}").json()["audio_consent"] is False


def test_orchestrator_interview_boundary_returns_typed_handoff(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    created = _create(client)
    response = client.post(
        "/api/agents/interview",
        headers={"Authorization": "Bearer test-recruiter"},
        json={
            "request_ref": "run-1/interview",
            "requested_outcome": "conduct",
            "context": {
                "candidate_ref": "candidate-1",
                "role_ref": "role-1",
                "role_version": "v1",
                "mode": "quick_screening",
                "session_id": created["id"],
                "source_refs": [
                    {"id": "company-1", "kind": "company", "content_hash": "sha256:company"},
                    {"id": "jd-1", "kind": "job_description", "content_hash": "sha256:jd"},
                    {"id": "cv-1", "kind": "cv", "content_hash": "sha256:abc"},
                ],
                "criteria": [
                    {"id": "current-role", "description": "Recent role and responsibilities"}
                ],
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["assignment"]["agent"] == "Interview"
    assert body["result"] == "Needs input"
    assert body["output"]["session_state"] == "ready"
    assert body["next_action"]["owner"] == "candidate"
