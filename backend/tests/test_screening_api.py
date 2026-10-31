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
            "cv_plain": "Candidate built Python APIs for several projects.",
            "max_topics": "1",
            "max_probes": "0",
        })
    assert response.status_code == 200
    return response.json()


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
