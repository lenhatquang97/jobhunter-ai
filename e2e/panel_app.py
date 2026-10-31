"""Deterministic PROVIDER fixture; production specialists still execute normally."""

import json

from app.main import create_app

from e2e.app import FixtureAdapter, RoutingFixture


class ControlledResponses:
    async def close(self):
        pass

    async def stream(self, messages, tools, role, **kwargs):
        if role == "Orchestrator":
            async for event in RoutingFixture().stream(messages, tools, role):
                yield event
            return
        data = json.loads(messages[0]["content"])
        data.update(role=role, handoff_name=tools[0]["name"] if tools else None)
        answers = [s for s in data["registry"] if s["model"] == "jobhunter.chat.answer"]
        if not tools and answers:
            source = answers[-1]
            answer = json.loads(source["excerpt"])["answer"]
            result = {
                "text": f"Submitted interview evidence (candidate self-report): {answer} [{source['key']}]"
            }
        elif not tools and data["case"].get("direct_guide") and not data["case"].get("interview"):
            result = {
                "text": f"The five-question typed interview guide is ready. Review it and choose Create private interview link. No answers have been submitted; interview evidence is incomplete. [{data['registry'][-1]['key']}]"
            }
        else:
            result = await FixtureAdapter().invoke(data, None)
        if tools:
            yield {
                "type": "tool.call",
                "name": tools[0]["name"],
                "id": "controlled-handoff",
                "arguments": json.dumps(result),
            }
        else:
            yield {"type": "text.delta", "text": result["text"]}


def create():
    return create_app(provider=ControlledResponses())
