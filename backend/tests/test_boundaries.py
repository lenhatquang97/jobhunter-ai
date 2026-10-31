import json

import httpx
import pytest
from app.bridge import OdooBridge
from app.config import Settings
from app.provider import ResponsesProvider
from app.schemas import SafeError
from conftest import Bridge, Provider, conversation, submission, terminal
from test_evidence import call, document


async def test_real_sdk_streaming_adapter_uses_tools_and_never_retries():
    requests = []
    events = [
        {
            "type": "response.output_text.delta",
            "delta": "Hello",
            "item_id": "msg-1",
            "output_index": 0,
            "content_index": 0,
            "sequence_number": 1,
            "logprobs": [],
        },
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "fc-1",
                "call_id": "tool-1",
                "name": "get_job",
                "arguments": '{"job_id":11}',
                "status": "completed",
            },
            "output_index": 1,
            "sequence_number": 2,
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp-1",
                "object": "response",
                "created_at": 0,
                "model": "gpt-6.1-sol",
                "output": [],
                "status": "completed",
            },
            "sequence_number": 3,
        },
    ]

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(e) + "\n\n" for e in events).encode(),
            )
        return httpx.Response(
            429, json={"error": {"message": "PRIVATE_PROVIDER_ERROR", "type": "rate_limit_error"}}
        )

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key="synthetic-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = ResponsesProvider(
        Settings(_env_file=None, openai_api_key="synthetic-key"), client=client
    )
    output = [
        event
        async for event in provider.stream(
            [{"role": "user", "content": "Hello"}], [], "Orchestrator"
        )
    ]
    assert output[0] == {"type": "text.delta", "text": "Hello"}
    assert output[-1] == {
        "type": "tool.call",
        "id": "tool-1",
        "name": "get_job",
        "arguments": '{"job_id":11}',
    }
    body = json.loads(requests[0].content)
    assert body["stream"] is True and body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["reasoning"] == {"effort": "low"}
    with pytest.raises(SafeError) as error:
        _ = [event async for event in provider.stream([], [], "Report")]
    assert "PRIVATE_PROVIDER_ERROR" not in str(error.value)
    assert len(requests) == 2
    await provider.close()


async def test_bridge_pinned_jsonrpc_no_redirect_and_safe_errors():
    seen = []

    async def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "run",
                    "result": {"ok": True, "data": {"job": {"id": 11}}, "sources": []},
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "error": {"data": {"debug": "PRIVATE_TRACE"}}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    bridge = OdooBridge(Settings(_env_file=None, odoo_base_url="http://pinned-odoo:8069"), client)
    output = await bridge.call(
        "run",
        "get_job",
        {"arguments": {"job_id": 11}, "context_refs": []},
        "synthetic-capability",
        "synthetic-db",
    )
    assert output["data"] == {"job": {"id": 11}}
    assert str(seen[0].url) == "http://pinned-odoo:8069/jobhunter/chat/tool"
    assert json.loads(seen[0].content)["params"] == {
        "run_id": "run",
        "operation": "get_job",
        "arguments": {"arguments": {"job_id": 11}, "context_refs": []},
    }
    with pytest.raises(SafeError) as error:
        await bridge.call("run", "get_job", {}, "synthetic-capability", "synthetic-db")
    assert "PRIVATE_TRACE" not in str(error.value)
    await bridge.close()


async def test_provider_request_and_tool_budgets_are_enforced(make_client):
    for maximum, expected_code in [
        ({"max_provider_requests": 2}, "request_limit"),
        ({"max_tool_calls": 2}, "tool_limit"),
    ]:
        provider = Provider([[call(cid=f"call-{i}")] for i in range(10)])
        bridge = Bridge()
        client, _ = await make_client(provider, bridge, **maximum)
        cid = await conversation(client)
        body = submission()
        await client.post(f"/v1/conversations/{cid}/messages", json=body)
        result = await terminal(client, body["run_id"])
        assert result["run"]["status"] == "failed"
        assert result["run"]["error"]["code"] == expected_code
        assert len([c for c in bridge.calls if c[0] == "get_job"]) <= 2


async def test_bridge_unallowlisted_data_is_excluded_from_model_and_public_events(make_client):
    bridge = Bridge()
    bridge.result["data"]["job"].update(
        {"secret_field": "PRIVATE_CONTACT", "email": "PRIVATE_EMAIL"}
    )
    provider = Provider(
        [
            [call()],
            [{"type": "text.delta", "text": "Internal"}],
            [{"type": "text.delta", "text": "Build reliable systems. [S1]"}],
        ]
    )
    client, _ = await make_client(provider, bridge)
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    result = await terminal(client, body["run_id"])
    assert result["run"]["status"] == "completed"
    assert "PRIVATE_" not in str(provider.inputs)
    assert "PRIVATE_" not in str(result)


async def test_read_document_extracts_sources_without_sending_raw_content(make_client):
    bridge = Bridge()
    bridge.result = {
        "ok": True,
        "data": {"document": document(b"Synthetic evidence")},
        "sources": [],
    }
    provider = Provider(
        [
            [call("read_document", {"attachment_id": 12})],
            [{"type": "text.delta", "text": "Internal"}],
            [{"type": "text.delta", "text": "Synthetic evidence [S1]"}],
        ]
    )
    client, _ = await make_client(provider, bridge)
    cid = await conversation(client)
    body = submission()
    await client.post(f"/v1/conversations/{cid}/messages", json=body)
    result = await terminal(client, body["run_id"])
    assert result["run"]["status"] == "completed"
    assert "content_base64" not in str(provider.inputs)
    assert "content_base64" not in str(result)
    assert "line:1" in str(result)


def test_production_configuration_rejects_sqlite_and_unpinned_urls():
    for overrides in (
        {"database_url": "sqlite+aiosqlite://"},
        {"odoo_base_url": "file:///tmp/socket"},
        {"odoo_base_url": "http://user:password@odoo"},
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **overrides)


async def test_clean_sse_eof_without_completed_event_is_failed_not_success():
    from openai import AsyncOpenAI

    async def handler(request):
        event = {
            "type": "response.output_text.delta",
            "delta": "Partial",
            "item_id": "msg",
            "output_index": 0,
            "content_index": 0,
            "sequence_number": 1,
            "logprobs": [],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=("data: " + json.dumps(event) + "\n\n").encode(),
        )

    sdk = AsyncOpenAI(
        api_key="synthetic",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = ResponsesProvider(Settings(_env_file=None, openai_api_key="synthetic"), client=sdk)
    chunks = []
    with pytest.raises(SafeError, match="provider_error"):
        async for event in provider.stream([], [], "Orchestrator"):
            chunks.append(event)
    assert chunks[0] == {"type": "text.delta", "text": "Partial"}
    await provider.close()


@pytest.mark.parametrize("count", [21, 100])
def test_document_metadata_list_accepts_bridge_bound_and_filters_fields(count):
    from app.tools import filter_data

    records = [
        {
            "id": index + 1,
            "name": f"Synthetic {index + 1}.txt",
            "mimetype": "text/plain",
            "file_size": 20,
            "checksum": "synthetic",
            "write_date": "2026-10-31 12:00:00",
            "res_model": "hr.job",
            "res_id": 11,
            "content_base64": "must-not-enter-model-input",
        }
        for index in range(count)
    ]
    result = filter_data("list_documents", {"documents": records})
    assert len(result["documents"]) == count
    assert result["documents"][-1] == {
        "id": count,
        "name": f"Synthetic {count}.txt",
        "mimetype": "text/plain",
        "file_size": 20,
        "checksum": "synthetic",
        "write_date": "2026-10-31 12:00:00",
        "res_model": "hr.job",
        "res_id": 11,
    }
    assert "content_base64" not in str(result)


@pytest.mark.parametrize(
    "operation,key,count",
    [
        ("list_documents", "documents", 101),
        ("search_jobs", "jobs", 21),
        ("search_applicants", "applicants", 21),
    ],
)
def test_tool_plural_result_rejects_above_its_specific_bound(operation, key, count):
    from app.tools import filter_data

    with pytest.raises(SafeError, match="invalid_tool_result"):
        filter_data(operation, {key: [{"id": index + 1} for index in range(count)]})
