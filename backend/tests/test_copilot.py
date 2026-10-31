"""Offline guardrail tests for the human-interview copilot."""

from unittest.mock import patch

from app import copilot


def request():
    return copilot.TranscriptRequest(
        context={
            "job_title": "Backend Engineer",
            "criteria": [
                {"id": "sql", "description": "Explains database trade-offs"},
                {"id": "ownership", "description": "Separates own contribution from team work"},
            ],
        },
        transcript=[
            {"id": "t1", "speaker": "interviewer", "text": "Tell me about the incident."},
            {"id": "t2", "speaker": "candidate", "text": "I captured traces; my teammate fixed the query."},
        ],
    )


def test_follow_up_drops_interviewer_evidence_reference():
    model = {"questions": [{
        "question": "Which traces did you capture?",
        "reason": "Clarify personal contribution",
        "based_on_turn_ids": ["t1", "t2"],
    }]}
    with patch.object(copilot, "_chat", return_value=model):
        result = copilot.suggest_follow_ups(request())
    assert result["questions"][0]["based_on_turn_ids"] == ["t2"]


def test_feedback_forces_unknown_without_candidate_evidence_and_fills_missing_criteria():
    model = {
        "summary": "The candidate described an incident.",
        "strengths": [],
        "gaps": ["SQL was not covered."],
        "findings": [{
            "criterion_id": "sql",
            "status": "supported",
            "statement": "The interviewer mentioned SQL.",
            "evidence_turn_ids": ["t1"],
        }],
    }
    with patch.object(copilot, "_chat", return_value=model):
        result = copilot.build_feedback(request())
    by_id = {item["criterion_id"]: item for item in result["findings"]}
    assert by_id["sql"]["status"] == "unknown"
    assert by_id["sql"]["evidence_turn_ids"] == []
    assert by_id["ownership"]["status"] == "unknown"


def test_same_language_translation_does_not_call_model():
    body = copilot.TranslationRequest(text="hello", source_language="en", target_language="en")
    with patch.object(copilot.httpx, "post") as post:
        assert copilot.translate_turn(body)["translation"] == "hello"
    post.assert_not_called()


def test_translation_uses_assemblyai_gateway(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "test-key")
    body = copilot.TranslationRequest(text="Hello", source_language="en", target_language="vi")
    response = type("Response", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: {"choices": [{"message": {"content": "Xin chào"}}]},
    })()
    with patch.object(copilot.httpx, "post", return_value=response) as post:
        result = copilot.translate_turn(body)
    assert result["translation"] == "Xin chào"
    assert post.call_args.args[0] == "https://llm-gateway.assemblyai.com/v1/chat/completions"
    assert post.call_args.kwargs["headers"]["authorization"] == "test-key"
