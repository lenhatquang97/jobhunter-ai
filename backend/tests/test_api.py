import asyncio
from uuid import uuid4

from conftest import Provider, conversation, headers, submission, terminal


async def test_authentication_and_normalized_owner_scope(make_client):
    client, _ = await make_client()
    cid = await conversation(client)
    assert (
        await client.get(f"/v1/conversations/{cid}", headers=headers(user=3))
    ).status_code == 404
    assert (
        await client.get(f"/v1/conversations/{cid}", headers=headers(companies=(2,)))
    ).status_code == 404
    assert (
        await client.get(f"/v1/conversations/{cid}", headers=headers(companies=(1, 1)))
    ).status_code == 200
    for auth in ("Bearer wrong", ""):
        assert (
            await client.get("/v1/conversations", headers={"Authorization": auth})
        ).status_code == 401
    assert (await client.get("/v1/conversations", headers=headers(companies=()))).status_code == 401
    assert (await client.get("/v1/conversations", headers=headers(user=1))).status_code == 401


async def test_persisted_events_replay_and_completion(make_client):
    client, _ = await make_client(
        Provider([[{"type": "text.delta", "text": "Xin "}, {"type": "text.delta", "text": "chào"}]])
    )
    cid = await conversation(client)
    body = submission()
    accepted = (await client.post(f"/v1/conversations/{cid}/messages", json=body)).json()
    final = await terminal(client, accepted["run_id"])
    assert final["run"]["status"] == "completed"
    assert [e["seq"] for e in final["events"]] == list(range(1, len(final["events"]) + 1))
    assert [e["type"] for e in final["events"]].count("agent.started") == 1
    assert final["events"][-1]["type"] == "run.completed"
    again = (await client.get(f"/v1/runs/{body['run_id']}/events?after_seq=2")).json()
    assert again["events"] == final["events"][2:]
    history = (await client.get(f"/v1/conversations/{cid}")).json()
    assert [m["text"] for m in history["messages"]] == ["Hello", "Xin chào"]
    assert (
        await client.get(f"/v1/runs/{body['run_id']}/events", headers=headers(user=5))
    ).status_code == 404
    assert (
        await client.post(f"/v1/runs/{body['run_id']}/cancel", headers=headers(user=5))
    ).status_code == 404


async def test_duplicate_concurrent_submissions_and_active_conflict(make_client):
    provider = Provider(wait=asyncio.Event())
    client, _ = await make_client(provider)
    cid = await conversation(client)
    body = submission()
    responses = await asyncio.gather(
        *[client.post(f"/v1/conversations/{cid}/messages", json=body) for _ in range(5)]
    )
    assert all(r.status_code == 200 for r in responses)
    assert {r.json()["run_id"] for r in responses} == {body["run_id"]}
    assert (
        await client.post(f"/v1/conversations/{cid}/messages", json=submission())
    ).status_code == 409
    await client.post(f"/v1/runs/{body['run_id']}/cancel")
    assert len(provider.inputs) <= 1
    history = (await client.get(f"/v1/conversations/{cid}")).json()
    assert len([m for m in history["messages"] if m["role"] == "user"]) == 1


async def test_duplicate_message_cannot_change_body_or_reuse_run_id(make_client):
    client, _ = await make_client()
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    await terminal(client, body["run_id"])
    assert (
        await client.post(f"/v1/conversations/{cid}/messages", json={**body, "text": "changed"})
    ).status_code == 409
    assert (
        await client.post(
            f"/v1/conversations/{cid}/messages", json=submission(run_id=body["run_id"])
        )
    ).status_code == 409


async def test_cancel_preserves_partial_and_is_terminal(make_client):
    from app.store import Run

    started = asyncio.Event()

    class Partial(Provider):
        async def stream(self, messages, tools, role):
            yield {"type": "text.delta", "text": "Partial draft"}
            started.set()
            await asyncio.Event().wait()

    client, app = await make_client(Partial())
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    await started.wait()
    result = await client.post(f"/v1/runs/{body['run_id']}/cancel")
    assert result.json()["status"] == "cancelled"
    final = await terminal(client, body["run_id"])
    assert final["events"][-1]["type"] == "run.cancelled"
    history = (await client.get(f"/v1/conversations/{cid}")).json()
    assert [message["role"] for message in history["messages"]] == ["user"]
    async with app.state.service.store.session() as session:
        stored = await session.get(Run, body["run_id"])
        assert stored.draft_text == "Partial draft"
    assert (await client.post(f"/v1/runs/{body['run_id']}/cancel")).json()["status"] == "cancelled"


async def test_provider_error_safe_and_timeout(make_client):
    for provider, overrides, code in [
        (Provider(error="API_KEY=very-private"), {}, "provider_error"),
        (Provider(wait=asyncio.Event()), {"run_timeout_seconds": 0.03}, "timeout"),
    ]:
        client, _ = await make_client(provider, **overrides)
        cid = await conversation(client)
        body = submission()
        await client.post(f"/v1/conversations/{cid}/messages", json=body)
        result = await terminal(client, body["run_id"])
        assert result["run"]["status"] == "failed"
        assert result["run"]["error"]["code"] == code
        assert "very-private" not in str(result)


async def test_missing_capability_needs_input_and_archive(make_client):
    provider = Provider()
    client, _ = await make_client(provider)
    cid = await conversation(client)
    body = submission(capability="")
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    assert (await terminal(client, body["run_id"]))["run"]["status"] == "needs_input"
    assert not provider.inputs
    await client.patch(f"/v1/conversations/{cid}", json={"archived": True})
    assert (await client.get("/v1/conversations")).json() == {"conversations": []}
    assert (
        await client.post(f"/v1/conversations/{cid}/messages", json=submission())
    ).status_code == 409


async def test_bad_input_and_retry_ownership(make_client):
    client, _ = await make_client()
    cid = await conversation(client)
    for override in (
        {"text": " "},
        {"context_refs": [{"model": "res.users", "id": 1}]},
        {"context_refs": [{"model": "hr.job", "id": -1}]},
        {"text": "x" * 16001},
    ):
        assert (
            await client.post(f"/v1/conversations/{cid}/messages", json=submission(**override))
        ).status_code == 422
    assert (
        await client.post(
            f"/v1/conversations/{cid}/messages", json=submission(retry_of_run_id=str(uuid4()))
        )
    ).status_code == 404


async def test_first_user_prompt_titles_private_history_without_later_renaming(make_client):
    for text, expected in [
        ("\n  Discuss\t  Python roles  \nSecond line is not the title", "Discuss Python roles"),
        ("New chat", "New chat"),
        ("A" * 100 + "\nExtra detail", "A" * 80),
    ]:
        provider = Provider(
            [
                [{"type": "text.delta", "text": "Assistant-derived title must not be used"}],
                [{"type": "text.delta", "text": "Another assistant answer"}],
            ]
        )
        client, _ = await make_client(provider)
        cid = await conversation(client)
        first = submission(text=text)
        await client.post(f"/v1/conversations/{cid}/messages", json=first)
        await terminal(client, first["run_id"])
        listed = (await client.get("/v1/conversations")).json()["conversations"]
        assert listed[0]["title"] == expected
        second = submission(text="Later user prompt must not rename the chat")
        await client.post(f"/v1/conversations/{cid}/messages", json=second)
        await terminal(client, second["run_id"])
        history = (await client.get(f"/v1/conversations/{cid}")).json()
        assert history["conversation"]["title"] == expected
        assert "Assistant-derived" not in history["conversation"]["title"]
