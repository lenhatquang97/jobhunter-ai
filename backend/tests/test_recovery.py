import asyncio
import contextlib
from uuid import uuid4

from app.config import Settings
from app.main import create_app
from app.schemas import Principal
from app.store import Message, Run
from conftest import Bridge, Provider, conversation, headers, submission, terminal
from test_evidence import evidence_provider


async def test_reused_evidence_inherits_provenance_and_revocation_hides_descendant(make_client):
    provider, bridge = evidence_provider(), Bridge()
    client, _ = await make_client(provider, bridge)
    cid = await conversation(client)
    first = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=first)
    await terminal(client, first["run_id"])
    provider.turns.extend(
        [
            [{"type": "text.delta", "text": "Reuse prior evidence"}],
            [{"type": "text.delta", "text": "Build reliable systems. [S1]"}],
        ]
    )
    second = submission(text="Summarize what you just found")
    await client.post(f"/v1/conversations/{cid}/messages", json=second)
    final = await terminal(client, second["run_id"])
    assert final["run"]["status"] == "completed"
    assert any(e["agent_id"] == "Report" for e in final["events"])
    bridge.state = "unavailable"
    history = (
        await client.get(f"/v1/conversations/{cid}", headers=headers(capability="fresh"))
    ).json()
    assert [m["text"] for m in history["messages"] if m["role"] == "assistant"] == ["", ""]


async def test_cancel_transitions_before_cancellation_resistant_late_result(make_client):
    released, started = asyncio.Event(), asyncio.Event()

    class Resistant(Provider):
        async def stream(self, messages, tools, role):
            started.set()
            with contextlib.suppress(asyncio.CancelledError):
                await released.wait()
            yield {"type": "text.delta", "text": "Late result must not be accepted"}

    client, _ = await make_client(Resistant())
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    await started.wait()
    assert (await client.post(f"/v1/runs/{body['run_id']}/cancel")).json()["status"] == "cancelled"
    final = await terminal(client, body["run_id"])
    assert "Late result" not in str(final)
    assert not any(e["type"] == "run.completed" for e in final["events"])


async def test_startup_interrupts_durable_active_run_without_provider_resume(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    settings = Settings(
        _env_file=None, database_url=url, allow_sqlite=True, bridge_secret="test-bridge-secret"
    )
    app = create_app(settings, provider=Provider(), bridge=Bridge())
    service = app.state.service
    await service.store.initialize()
    principal = Principal(database_uuid="synthetic-db", user_id=2, company_ids=[1])
    cid = (await service.create_conversation(principal))["id"]
    rid = str(uuid4())
    async with service.store.session() as session, session.begin():
        session.add(
            Run(
                id=rid,
                conversation_id=cid,
                client_message_id=str(uuid4()),
                fingerprint="hash",
                status="running",
                draft_text="Persisted draft",
            )
        )
        await session.flush()
        session.add(
            Message(conversation_id=cid, run_id=rid, role="assistant", text="Persisted draft")
        )
    await service.store.close()
    provider = Provider()
    restarted = create_app(settings, provider=provider, bridge=Bridge())
    async with restarted.router.lifespan_context(restarted):
        replay = await restarted.state.service.events(rid, principal, "", "", 0)
        assert replay["run"]["status"] == "interrupted"
        assert replay["events"][-1]["type"] == "run.interrupted"
        history = await restarted.state.service.history(cid, principal, "", "")
        assert history["messages"] == []
        async with restarted.state.service.store.session() as session:
            stored = await session.get(Run, rid)
            assert stored.draft_text == "Persisted draft"
        assert not provider.inputs
        from app.schemas import Submit

        next_body = Submit.model_validate(submission(text="Continue"))
        accepted = await restarted.state.service.submit(cid, principal, next_body)
        await restarted.state.service.tasks[accepted["run_id"]]
        assert "Persisted draft" not in str(provider.inputs[-1][0])


async def test_invalid_citation_draft_is_withheld_from_history(make_client):
    client, _ = await make_client(evidence_provider('"Secret invented fact" [S1]'))
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    await terminal(client, body["run_id"])
    history = (
        await client.get(f"/v1/conversations/{cid}", headers=headers(capability="fresh"))
    ).json()
    assert "Secret invented fact" not in str(history)


async def test_sensitive_validation_payload_is_not_echoed(make_client):
    client, _ = await make_client()
    cid = await conversation(client)
    result = await client.post(
        f"/v1/conversations/{cid}/messages", json=submission(capability="super-secret" * 900)
    )
    assert result.status_code == 422
    assert "super-secret" not in result.text


async def test_replay_with_new_proposed_run_and_refreshed_grant_returns_original(make_client):
    client, _ = await make_client()
    cid = await conversation(client)
    original = submission(
        context_refs=[{"model": "hr.job", "id": 11, "label": "Old display label"}]
    )
    await client.post(f"/v1/conversations/{cid}/messages", json=original)
    await terminal(client, original["run_id"])
    replay = {
        **original,
        "run_id": str(uuid4()),
        "capability": "refreshed-grant",
        "context_refs": [{"model": "hr.job", "id": 11, "label": "New display label"}],
    }
    result = await client.post(f"/v1/conversations/{cid}/messages", json=replay)
    assert result.status_code == 200
    assert result.json()["run_id"] == original["run_id"]


async def test_history_validation_grant_uses_explicit_validation_run_identity(make_client):
    bridge = Bridge()
    provider = evidence_provider()
    client, _ = await make_client(provider, bridge)
    cid = await conversation(client)
    first = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=first)
    await terminal(client, first["run_id"])
    expected_grant_id = str(uuid4())
    original_call = bridge.call

    async def grant_bound_call(run_id, operation, arguments, capability, database):
        if capability == "history-only" and run_id != expected_grant_id:
            return {"ok": False, "data": {}, "sources": []}
        return await original_call(run_id, operation, arguments, capability, database)

    bridge.call = grant_bound_call
    request_headers = {
        **headers(capability="history-only"),
        "X-Odoo-Validation-Run": expected_grant_id,
    }
    history = (await client.get(f"/v1/conversations/{cid}", headers=request_headers)).json()
    assert history["messages"][-1]["text"] == "Build reliable systems. [S1]"
    events = (
        await client.get(f"/v1/runs/{first['run_id']}/events", headers=request_headers)
    ).json()
    assert events["run"]["source_state"] == "current"


async def test_active_grant_reuses_history_with_current_run_identity(make_client):
    bridge = Bridge()
    provider = evidence_provider()
    client, _ = await make_client(provider, bridge)
    cid = await conversation(client)
    first = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=first)
    await terminal(client, first["run_id"])
    second = submission(text="Summarize again", capability="second-execution")
    original_call = bridge.call

    async def grant_bound_call(run_id, operation, arguments, capability, database):
        if capability == "second-execution" and run_id != second["run_id"]:
            return {"ok": False, "data": {}, "sources": []}
        return await original_call(run_id, operation, arguments, capability, database)

    bridge.call = grant_bound_call
    provider.turns.extend(
        [
            [{"type": "text.delta", "text": "Internal"}],
            [{"type": "text.delta", "text": "Build reliable systems. [S1]"}],
        ]
    )
    await client.post(f"/v1/conversations/{cid}/messages", json=second)
    result = await terminal(client, second["run_id"])
    assert result["run"]["status"] == "completed"
    assert any(e["agent_id"] == "Report" for e in result["events"])


async def test_queued_execution_cannot_revive_durable_cancelled_run(make_client):
    import pytest

    provider = Provider()
    client, app = await make_client(provider)
    cid = await conversation(client)
    body = submission()
    from app.schemas import Submit

    principal = Principal(database_uuid="synthetic-db", user_id=2, company_ids=[1])
    async with app.state.service.store.session() as session, session.begin():
        session.add(
            Run(
                id=body["run_id"],
                conversation_id=cid,
                client_message_id=body["client_message_id"],
                fingerprint="hash",
                status="cancelled",
            )
        )
    with pytest.raises(asyncio.CancelledError):
        await app.state.service._execute(body["run_id"], principal, Submit.model_validate(body))
    result = await terminal(client, body["run_id"])
    assert result["run"]["status"] == "cancelled"
    assert not provider.inputs


async def test_history_validation_has_overall_deadline_and_fails_closed(make_client):
    provider, bridge = evidence_provider(), Bridge()
    client, _ = await make_client(provider, bridge, bridge_timeout_seconds=0.03)
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    await terminal(client, body["run_id"])
    original = bridge.call

    async def hung_validation(run_id, operation, arguments, capability, database):
        if operation == "validate_sources":
            await asyncio.Event().wait()
        return await original(run_id, operation, arguments, capability, database)

    bridge.call = hung_validation
    response = await asyncio.wait_for(
        client.get(f"/v1/conversations/{cid}", headers=headers(capability="fresh")), timeout=0.15
    )
    assert response.status_code == 200
    assert response.json()["messages"][-1]["text"] == ""
    assert response.json()["messages"][-1]["visibility"] == "unavailable"
