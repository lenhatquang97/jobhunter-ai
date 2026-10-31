"""Exercise migrated specialists through their bounded runtime, not replacement adapters."""

import json

import pytest
from app.adapters import AdapterRegistry
from app.schemas import SafeError


class Runtime:
    def __init__(self, result, name=None):
        self.result, self.name, self.calls = result, name, []

    async def stream(self, messages, tools, instructions=None):
        self.calls.append((messages, tools, instructions))
        if self.name:
            yield {
                "type": "tool.call",
                "id": "fixture",
                "name": self.name,
                "arguments": json.dumps(self.result),
            }
        else:
            yield {"type": "text.delta", "text": self.result}


def assignment(role, name=None):
    return {
        "role": role,
        "contract_version": "2.0",
        "request": "Prepare in Vietnamese",
        "case": {
            "applicant_id": 21,
            "criteria": [{"id": "C1", "text": "Python"}],
            "interview": {"submitted_count": 0, "answers": []},
        },
        "registry": [
            {
                "key": "S1",
                "model": "hr.applicant",
                "record_id": 21,
                "attachment_id": 31,
                "locator": "line:1",
                "excerpt": "Built Python services.",
            }
        ],
        "handoff_name": name,
        "mode": "guide" if name else "workflow",
    }


def configured():
    return AdapterRegistry.configured(
        json.dumps(
            {
                role: f"app.agents:{role.lower()}_adapter"
                for role in ("Filter", "Interview", "Report")
            }
        )
    )


async def test_imported_filter_uses_strict_handoff_and_shared_gateway():
    registry = configured()
    result = {
        "summary": "CV evidence",
        "findings": [
            {
                "criterion_id": "C1",
                "status": "partial",
                "finding": "Self-reported Python work.",
                "citations": [{"source_key": "S1", "quote": "Built Python services."}],
                "question": "Describe your contribution.",
            }
        ],
    }
    runtime = Runtime(result, "submit_screening")
    assert (
        await registry.invoke("Filter", assignment("Filter", "submit_screening"), runtime) == result
    )
    messages, tools, instructions = runtime.calls[0]
    assert tools[0]["name"] == "submit_screening" and tools[0]["strict"]
    assert "unknown" in instructions and "UNTRUSTED" in instructions
    assert "Built Python services." in json.dumps(messages)
    assert "score" not in tools[0]["parameters"]["properties"]


async def test_imported_interview_enforces_five_questions_and_no_automatic_followups():
    registry = configured()
    questions = [
        {"criterion_id": "C1", "question": f"Câu hỏi {i + 1}?", "follow_up": None} for i in range(5)
    ]
    runtime = Runtime({"language": "vi", "questions": questions}, "submit_guide")
    got = await registry.invoke("Interview", assignment("Interview", "submit_guide"), runtime)
    assert got["language"] == "vi" and len(got["questions"]) == 5
    assert "five" in runtime.calls[0][2]
    bad = Runtime({"language": "en", "questions": questions[:2]}, "submit_guide")
    with pytest.raises(SafeError, match="handoff"):
        await registry.invoke("Interview", assignment("Interview", "submit_guide"), bad)


async def test_report_calls_real_gateway_and_preserves_incomplete_participation():
    runtime = Runtime("No interview answers submitted; evidence is incomplete. [S1]")
    assert (await configured().invoke("Report", assignment("Report"), runtime))["text"].endswith(
        "[S1]"
    )
    assert runtime.calls[0][1] == []
    assert "incomplete" in runtime.calls[0][2]
    assert "suitability" in runtime.calls[0][2]


async def test_wrong_handoff_and_scores_are_rejected():
    for result, name in [
        ({"summary": "wrong", "score": 99, "findings": []}, "submit_screening"),
        ({"summary": "wrong", "findings": []}, "arbitrary_write"),
    ]:
        with pytest.raises(SafeError):
            await configured().invoke(
                "Filter", assignment("Filter", "submit_screening"), Runtime(result, name)
            )
