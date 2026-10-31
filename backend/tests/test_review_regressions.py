import asyncio
from uuid import uuid4

import pytest
from app.store import Run
from conftest import Bridge, Provider, conversation, headers, submission, terminal
from test_evidence import call


async def test_report_replaces_preamble_and_exact_completed_text_is_validated(make_client):
    provider = Provider(
        [
            [{"type": "text.delta", "text": '"Invented preamble" [S999]. '}, call()],
            [{"type": "text.delta", "text": "Internal handoff"}],
            [{"type": "text.delta", "text": "Build reliable systems. [S1]"}],
        ]
    )
    client, _ = await make_client(provider)
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    final = await terminal(client, body["run_id"])
    assert final["run"]["status"] == "completed"
    completed = next(event for event in final["events"] if event["type"] == "message.completed")
    assert completed["payload"]["text"] == "Build reliable systems. [S1]"
    assert "Invented preamble" not in str(final)
    history = (
        await client.get(f"/v1/conversations/{cid}", headers=headers(capability="fresh"))
    ).json()
    assert history["messages"][-1]["text"] == "Build reliable systems. [S1]"


@pytest.mark.parametrize("outcome", ["cancelled", "failed", "interrupted"])
async def test_unvalidated_report_draft_hidden_mid_run_and_after_terminal_and_not_reused(
    make_client, outcome
):
    report_started, release = asyncio.Event(), asyncio.Event()

    class PausedReport(Provider):
        async def stream(self, messages, tools, role):
            if role != "Report":
                async for event in super().stream(messages, tools, role):
                    yield event
                return
            yield {"type": "text.delta", "text": '"Invented draft" [S99]'}
            report_started.set()
            await release.wait()
            raise RuntimeError("Synthetic failure during Report")

    provider = PausedReport([[call()], [{"type": "text.delta", "text": "Internal"}]])
    client, app = await make_client(provider)
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    await report_started.wait()
    during = (
        await client.get(f"/v1/conversations/{cid}", headers=headers(capability="fresh"))
    ).json()
    assert "Invented draft" not in str(during)
    assert not [message for message in during["messages"] if message["role"] == "assistant"]
    if outcome == "cancelled":
        await client.post(f"/v1/runs/{body['run_id']}/cancel")
    elif outcome == "failed":
        release.set()
    else:
        app.state.service.stopping = True
        app.state.service.tasks[body["run_id"]].cancel()
    final = await terminal(client, body["run_id"])
    assert final["run"]["status"] == outcome
    assert "Invented draft" not in str(final)
    async with app.state.service.store.session() as session:
        stored = await session.get(Run, body["run_id"])
        assert stored.draft_text == '"Invented draft" [S99]'
    history = (
        await client.get(f"/v1/conversations/{cid}", headers=headers(capability="fresh"))
    ).json()
    assert "Invented draft" not in str(history)
    app.state.service.stopping = False
    provider.turns.append([{"type": "text.delta", "text": "A new greeting"}])
    next_body = submission(text="Continue")
    await client.post(f"/v1/conversations/{cid}/messages", json=next_body)
    assert (await terminal(client, next_body["run_id"]))["run"]["status"] == "completed"
    assert "Invented draft" not in str(provider.inputs[-1][0])


async def test_context_duplicate_pairs_are_one_logical_idempotent_context(make_client):
    provider = Provider(wait=asyncio.Event())
    bridge = Bridge()
    client, _ = await make_client(provider, bridge)
    cid = await conversation(client)
    body = submission(
        context_refs=[
            {"model": "hr.job", "id": 11},
            {"model": "hr.job", "id": 11, "label": "duplicate"},
        ]
    )
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    replay = {**body, "run_id": str(uuid4()), "context_refs": [{"model": "hr.job", "id": 11}]}
    result = await client.post(f"/v1/conversations/{cid}/messages", json=replay)
    assert result.status_code == 200
    assert result.json()["run_id"] == body["run_id"]
    await client.post(f"/v1/runs/{body['run_id']}/cancel")


async def test_callback_context_is_sorted_unique_and_labels_are_not_evidence(make_client):
    bridge = Bridge()
    provider = Provider(
        [
            [call()],
            [{"type": "text.delta", "text": "Internal"}],
            [{"type": "text.delta", "text": "Build reliable systems. [S1]"}],
        ]
    )
    client, _ = await make_client(provider, bridge)
    cid = await conversation(client)
    body = submission(
        context_refs=[
            {"model": "hr.job", "id": 11, "label": "Untrusted label"},
            {"model": "hr.job", "id": 11},
        ]
    )
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    final = await terminal(client, body["run_id"])
    assert final["run"]["status"] == "completed"
    assert bridge.calls[0][1]["context_refs"] == [{"model": "hr.job", "id": 11}]
    assert "Untrusted label" not in str(provider.inputs)
