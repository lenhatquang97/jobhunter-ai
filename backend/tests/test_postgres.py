"""Optional actual PostgreSQL integration; enabled by the disposable-cluster helper."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from app.config import Settings
from app.main import create_app
from app.store import Run
from conftest import Bridge, Provider, headers, submission
from sqlalchemy.exc import IntegrityError


@pytest.mark.skipif(
    not os.environ.get("JOBHUNTER_TEST_POSTGRES_URL"),
    reason="Disposable PostgreSQL URL not supplied",
)
async def test_postgres_durable_active_constraint_events_owner_and_restart():
    settings = Settings(
        _env_file=None,
        database_url=os.environ["JOBHUNTER_TEST_POSTGRES_URL"],
        bridge_secret="test-bridge-secret",
    )
    provider = Provider(wait=asyncio.Event())
    app = create_app(settings, provider=provider, bridge=Bridge())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers()
        ) as client,
    ):
        cid = (await client.post("/v1/conversations", json={})).json()["id"]
        body = submission()
        accepted = await client.post(f"/v1/conversations/{cid}/messages", json=body)
        assert accepted.status_code == 200
        # Exercise the actual PostgreSQL partial unique index, bypassing process mutex.
        with pytest.raises(IntegrityError):
            async with app.state.service.store.session() as session, session.begin():
                session.add(
                    Run(
                        id=str(uuid4()),
                        conversation_id=cid,
                        client_message_id=str(uuid4()),
                        fingerprint="test",
                        status="queued",
                    )
                )
                await session.flush()
        assert (
            await client.get(f"/v1/conversations/{cid}", headers=headers(user=7))
        ).status_code == 404
    restarted_provider = Provider()
    restarted = create_app(settings, provider=restarted_provider, bridge=Bridge())
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://test", headers=headers()
        ) as client,
    ):
        replay = (await client.get(f"/v1/runs/{body['run_id']}/events")).json()
        assert replay["run"]["status"] == "interrupted"
        assert replay["events"][-1]["type"] == "run.interrupted"
        assert not restarted_provider.inputs
        history = (await client.get(f"/v1/conversations/{cid}")).json()
        assert history["messages"][0]["text"] == "Hello"
        duplicate = await client.post(
            f"/v1/conversations/{cid}/messages", json={**body, "run_id": str(uuid4())}
        )
        assert duplicate.json()["run_id"] == body["run_id"]
