import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest
from app.config import Settings
from app.main import create_app


class Provider:
    def __init__(self, turns=None, wait=None, error=None):
        self.turns = list(turns or [[{"type": "text.delta", "text": "Hello"}]])
        self.inputs = []
        self.wait = wait
        self.error = error

    async def stream(self, messages, tools, role):
        self.inputs.append((messages.copy(), tools, role))
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise RuntimeError(self.error)
        for event in self.turns.pop(0):
            await asyncio.sleep(0)
            yield event


SOURCE = {
    "key": "odoo-job",
    "model": "hr.job",
    "record_id": 11,
    "field": "description",
    "title": "Synthetic role",
    "write_date": "2026-10-31 12:00:00",
    "locator": "field:description",
    "excerpt": "Build reliable systems.",
}


class Bridge:
    def __init__(self):
        self.calls = []
        self.state = "current"
        self.fail_validation = False
        self.result = {
            "ok": True,
            "data": {
                "job": {
                    "id": 11,
                    "name": "Synthetic role",
                    "description": "Build reliable systems.",
                }
            },
            "sources": [SOURCE],
        }

    async def call(self, run_id, operation, arguments, capability, database):
        self.calls.append((operation, arguments, capability))
        if operation == "validate_sources":
            if self.fail_validation:
                raise RuntimeError("secret bridge detail")
            return {
                "ok": True,
                "data": {
                    "states": [{"key": s["key"], "state": self.state} for s in arguments["sources"]]
                },
                "sources": [],
            }
        return self.result


def headers(user=2, companies=(1,), capability=None):
    p = {"database_uuid": "synthetic-db", "user_id": user, "company_ids": list(companies)}
    result = {
        "Authorization": "Bearer test-bridge-secret",
        "X-Jobhunter-Principal": base64.urlsafe_b64encode(json.dumps(p).encode())
        .decode()
        .rstrip("="),
    }
    if capability:
        result.update({"X-Odoo-Capability": capability, "X-Odoo-Database": "synthetic"})
    return result


@pytest.fixture
async def make_client(tmp_path):
    clients = []

    async def factory(provider=None, bridge=None, adapters=None, **overrides):
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path / (str(uuid4()) + '.db')}",
            bridge_secret="test-bridge-secret",
            allow_sqlite=True,
            **overrides,
        )
        app = create_app(
            settings, provider=provider or Provider(), bridge=bridge or Bridge(), adapters=adapters
        )
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers()
        )
        clients.append((client, lifespan))
        return client, app

    yield factory
    for client, lifespan in reversed(clients):
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


async def conversation(client):
    response = await client.post("/v1/conversations", json={})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def submission(**overrides):
    return {
        "run_id": str(uuid4()),
        "client_message_id": str(uuid4()),
        "text": "Hello",
        "context_refs": [],
        "capability": "signed-test-capability",
        "odoo_database": "synthetic",
        **overrides,
    }


async def terminal(client, run_id):
    for _ in range(200):
        result = await client.get(
            f"/v1/runs/{run_id}/events", headers=headers(capability="validation")
        )
        if result.json()["run"]["status"] not in ("queued", "running"):
            return result.json()
        await asyncio.sleep(0.01)
    raise AssertionError("Run did not finish")
