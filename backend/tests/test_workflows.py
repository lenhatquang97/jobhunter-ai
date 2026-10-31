import asyncio
import base64
import copy
import json

import pytest
from app.adapters import AdapterRegistry
from conftest import SOURCE, Bridge, Provider, conversation, headers, submission, terminal


class TestAdapter:
    async def invoke(self, assignment, runtime):
        chunks, handoffs = [], []
        async for event in runtime.stream(
            [{"role": "user", "content": json.dumps(assignment)}], []
        ):
            if event["type"] == "tool.call":
                handoffs.append(json.loads(event["arguments"]))
            elif event["type"] == "text.delta":
                chunks.append(event["text"])
        return handoffs[0] if handoffs else {"text": "".join(chunks)}


@pytest.fixture
async def workflow_client(make_client):
    async def factory(provider=None, bridge=None, **kwargs):
        return await make_client(
            provider,
            bridge,
            adapters=AdapterRegistry(
                {r: TestAdapter() for r in ("Filter", "Schedule", "Interview", "Report")}
            ),
            **kwargs,
        )

    return factory


def handoff(name, data):
    return [{"type": "tool.call", "id": name, "name": name, "arguments": json.dumps(data)}]


def request(action="screen", **data):
    return submission(
        text="Check this candidate CV",
        context_refs=[{"model": "hr.applicant", "id": 21}],
        workflow={"action": action, "applicant_id": 21, "data": data},
    )


class WorkflowBridge(Bridge):
    def __init__(self, approved=True):
        super().__init__()
        self.case = {
            "id": 1,
            "applicant_id": 21,
            "job_id": 11,
            "version": 1,
            "applicant_name": "Synthetic candidate",
            "candidate_email": "netlink269@gmail.com",
            "criteria_version": "v1",
            "criteria_approved": approved,
            "criteria": [{"id": "c1", "text": "Python experience"}],
            "documents": [{"id": 31, "name": "CV.txt"}],
            "screening": None,
            "schedule": None,
            "interview": None,
            "report": None,
            "interview_summary": None,
        }
        self.artifacts = []

    async def call(self, run_id, operation, arguments, capability, database):
        if operation == "validate_sources":
            return await super().call(run_id, operation, arguments, capability, database)
        self.calls.append((operation, arguments, capability))
        args = arguments["arguments"]
        if operation == "workflow_context":
            return {"ok": True, "data": {"case": copy.deepcopy(self.case)}, "sources": [SOURCE]}
        if operation == "read_document":
            return {
                "ok": True,
                "data": {
                    "document": {
                        "id": 31,
                        "name": "CV.txt",
                        "mimetype": "text/plain",
                        "file_size": 24,
                        "content_base64": base64.b64encode(b"Built Python services.").decode(),
                        "checksum": "fixture",
                        "write_date": SOURCE["write_date"],
                        "res_model": "hr.applicant",
                        "res_id": 21,
                    }
                },
                "sources": [],
            }
        if operation == "workflow_save":
            self.artifacts.append(copy.deepcopy(args))
            self.case[args["kind"]] = {
                "id": len(self.artifacts),
                "kind": args["kind"],
                "content": args["content"],
                "sources": args["sources"],
                "source_state": "current",
                "version": 1,
            }
        elif operation == "workflow_schedule":
            self.case["schedule"] = {
                "version": 1,
                "status": "needs_input",
                "missing": ["participants"],
                "issues": [],
                "schedule": args["schedule"],
                "guide_ready": False,
                "booking": None,
            }
        elif operation == "workflow_confirm":
            self.case["schedule"]["booking"] = {
                "event_id": 50,
                "session_id": 60,
                "mail_status": "outgoing",
            }
        else:
            raise AssertionError(operation)
        self.case["version"] += 1
        return {"ok": True, "data": {"case": copy.deepcopy(self.case)}, "sources": []}


async def test_workflow_requires_selected_candidate_and_preserves_old_clients(workflow_client):
    client, _ = await workflow_client()
    cid = await conversation(client)
    body = request()
    body["context_refs"] = []
    response = await client.post(f"/v1/conversations/{cid}/messages", json=body)
    assert response.status_code == 422
    old = (await client.post(f"/v1/conversations/{cid}/messages", json=submission())).json()
    assert (await terminal(client, old["run_id"]))["run"]["status"] == "completed"


async def test_screening_waits_for_explicit_criteria_approval(workflow_client):
    provider, bridge = Provider(), WorkflowBridge(approved=False)
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request())
    assert response.status_code == 200
    result = await terminal(client, response.json()["run_id"])
    assert result["run"]["status"] == "needs_input"
    assert not provider.inputs
    assert [c[0] for c in bridge.calls if c[0] != "validate_sources"] == ["workflow_context"]


async def test_actual_filter_and_report_persist_cited_screening(workflow_client):
    screening = {
        "summary": "Candidate reports Python experience",
        "findings": [
            {
                "criterion_id": "c1",
                "status": "supported",
                "finding": "Candidate reports Python use",
                "citations": [{"source_key": "S2", "quote": "Built Python services."}],
                "question": "What was your own contribution?",
            }
        ],
    }
    provider = Provider(
        [
            handoff("submit_screening", screening),
            [{"type": "text.delta", "text": "Candidate reports Python use. [S2]"}],
        ]
    )
    bridge = WorkflowBridge()
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request())
    assert response.status_code == 200
    final = await terminal(client, response.json()["run_id"])
    assert final["run"]["status"] == "completed", final
    assert [i[2] for i in provider.inputs] == ["Filter", "Report"]
    assert bridge.artifacts[0]["kind"] == "screening"
    history = (
        await client.get(f"/v1/conversations/{cid}", headers=headers(capability="validation"))
    ).json()
    assert history["candidate_ids"] == [21]
    assert history["focused_applicant_id"] == 21
    assert history["messages"][-1]["text"].endswith("[S2]")


async def test_filter_rejects_invented_quote_and_missing_criterion(workflow_client):
    bad = {
        "summary": "invented",
        "findings": [
            {
                "criterion_id": "c1",
                "status": "supported",
                "finding": "invented",
                "citations": [{"source_key": "S2", "quote": "Never said this"}],
                "question": "",
            }
        ],
    }
    provider, bridge = Provider([handoff("submit_screening", bad)]), WorkflowBridge()
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request())
    assert response.status_code == 200
    result = await terminal(client, response.json()["run_id"])
    assert result["run"]["status"] == "failed"
    assert not bridge.artifacts


async def test_report_without_answers_remains_explicitly_incomplete(workflow_client):
    provider = Provider(
        [
            [
                {
                    "type": "text.delta",
                    "text": "No submitted interview answers are available; evaluation is incomplete. [S1]",
                }
            ]
        ]
    )
    bridge = WorkflowBridge()
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request("report"))
    assert response.status_code == 200
    result = await terminal(client, response.json()["run_id"])
    assert result["run"]["status"] == "completed", result
    assert [i[2] for i in provider.inputs] == ["Report"]
    assert bridge.artifacts[-1]["kind"] == "report"


def test_workflow_schemas_forbid_scores_and_bound_questions():
    from app.workflow_schemas import Guide, Screening

    with pytest.raises(ValueError):
        Screening.model_validate({"summary": "x", "score": 99, "findings": []})
    with pytest.raises(ValueError):
        Guide.model_validate({"language": "en", "questions": []})


async def test_actual_schedule_needs_input_without_booking_or_interview(workflow_client):
    provider, bridge = Provider([handoff("submit_schedule", {})]), WorkflowBridge()
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request("schedule"))
    final = await terminal(client, response.json()["run_id"])
    assert final["run"]["status"] == "needs_input"
    assert [i[2] for i in provider.inputs] == ["Schedule"]
    assert "workflow_confirm" not in [c[0] for c in bridge.calls]


async def test_natural_routing_uses_actual_selected_context_and_shared_budget(workflow_client):
    provider = Provider([handoff("candidate_workflow", {"action": "report", "applicant_id": 21})])
    client, _ = await workflow_client(provider, WorkflowBridge(), max_provider_requests=1)
    cid = await conversation(client)
    body = request("report")
    body.pop("workflow")
    response = await client.post(f"/v1/conversations/{cid}/messages", json=body)
    final = await terminal(client, response.json()["run_id"])
    assert final["run"]["status"] == "failed"
    assert final["run"]["error"]["code"] == "request_limit"
    assert [i[2] for i in provider.inputs] == ["Orchestrator"]


async def test_ambiguous_natural_routing_requests_selection(workflow_client):
    provider = Provider([handoff("candidate_workflow", {"action": "screen", "applicant_id": None})])
    client, _ = await workflow_client(provider, WorkflowBridge())
    cid = await conversation(client)
    body = submission(
        text="Check that candidate",
        context_refs=[{"model": "hr.applicant", "id": 21}, {"model": "hr.applicant", "id": 22}],
    )
    response = await client.post(f"/v1/conversations/{cid}/messages", json=body)
    final = await terminal(client, response.json()["run_id"])
    assert final["run"]["status"] == "needs_input"
    assert final["run"]["error"]["code"] == "ambiguous_context"


async def test_same_candidate_in_multiple_private_chats_and_duplicate_workflow(workflow_client):
    bridge = WorkflowBridge(approved=False)
    client, app = await workflow_client(bridge=bridge)
    first, second = await conversation(client), await conversation(client)
    for cid in (first, second):
        body = request()
        original = (await client.post(f"/v1/conversations/{cid}/messages", json=body)).json()
        await terminal(client, original["run_id"])
        duplicate = (await client.post(f"/v1/conversations/{cid}/messages", json=body)).json()
        assert duplicate["run_id"] == original["run_id"]
        history = (
            await client.get(f"/v1/conversations/{cid}", headers=headers(capability="validation"))
        ).json()
        assert history["candidate_ids"] == [21]
        assert len(history["messages"]) == 1
        assert (
            await client.get(f"/v1/conversations/{cid}", headers=headers(user=3))
        ).status_code == 404


async def test_source_revocation_masks_workflow_events(workflow_client):
    bridge = WorkflowBridge(approved=False)
    client, _ = await workflow_client(bridge=bridge)
    cid = await conversation(client)
    result = (await client.post(f"/v1/conversations/{cid}/messages", json=request())).json()
    await terminal(client, result["run_id"])
    bridge.state = "unavailable"
    final = await terminal(client, result["run_id"])
    workflow_events = [e for e in final["events"] if e["type"] == "workflow.updated"]
    assert workflow_events and workflow_events[0]["payload"] == {"visibility": "unavailable"}


async def test_missing_friend_adapter_is_visible_without_provider_fallback(make_client):
    provider = Provider()
    client, _ = await make_client(provider, WorkflowBridge())
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request())
    result = await terminal(client, response.json()["run_id"])
    assert result["run"]["status"] == "needs_input"
    assert result["run"]["error"]["code"] == "adapter_unavailable"
    assert not provider.inputs
    assert not [
        e for e in result["events"] if e["type"] == "agent.started" and e["agent_id"] == "Filter"
    ]


async def test_cancellation_swallowing_adapter_cannot_start_new_model_call(make_client):
    entered = asyncio.Event()

    class DelayedAdapter:
        async def invoke(self, assignment, runtime):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                async for _ in runtime.stream([], []):
                    pass
            return {"summary": "late", "findings": []}

    provider = Provider()
    client, _ = await make_client(
        provider, WorkflowBridge(), adapters=AdapterRegistry({"Filter": DelayedAdapter()})
    )
    cid = await conversation(client)
    accepted = (await client.post(f"/v1/conversations/{cid}/messages", json=request())).json()
    await asyncio.wait_for(entered.wait(), 2)
    await client.post(f"/v1/runs/{accepted['run_id']}/cancel")
    assert not provider.inputs


async def test_schedule_activity_omits_private_contact_windows(workflow_client):
    provider = Provider([handoff("submit_schedule", {"candidate_email": "netlink269@gmail.com"})])
    client, _ = await workflow_client(provider, WorkflowBridge())
    cid = await conversation(client)
    result = (
        await client.post(f"/v1/conversations/{cid}/messages", json=request("schedule"))
    ).json()
    final = await terminal(client, result["run_id"])
    started = next(
        e
        for e in final["events"]
        if e["type"] == "tool.started" and e["payload"]["name"] == "workflow_schedule"
    )
    assert "netlink269@gmail.com" not in json.dumps(started["payload"])


async def test_natural_route_retains_prior_document_source_and_masks_old_activity(workflow_client):
    class RevocableBridge(WorkflowBridge):
        revoked = False

        async def call(self, run_id, operation, arguments, capability, database):
            if operation == "validate_sources":
                return {
                    "ok": True,
                    "data": {
                        "states": [
                            {
                                "key": s["key"],
                                "state": "unavailable"
                                if self.revoked and s.get("attachment_id") == 31
                                else "current",
                            }
                            for s in arguments["sources"]
                        ]
                    },
                    "sources": [],
                }
            return await super().call(run_id, operation, arguments, capability, database)

    provider = Provider(
        [
            handoff("read_document", {"attachment_id": 31}),
            handoff("candidate_workflow", {"action": "report", "applicant_id": 21}),
            [{"type": "text.delta", "text": "The candidate self-reports Python use. [S1]"}],
        ]
    )
    bridge = RevocableBridge()
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    body = request("report")
    body.pop("workflow")
    response = (await client.post(f"/v1/conversations/{cid}/messages", json=body)).json()
    final = await terminal(client, response["run_id"])
    assert final["run"]["status"] == "completed", final
    bridge.revoked = True
    after = await terminal(client, response["run_id"])
    assert after["run"]["source_state"] == "unavailable"
    assert all(
        e["payload"] == {"visibility": "unavailable"}
        for e in after["events"]
        if e["type"].startswith("tool.")
    )


async def test_deadline_swallowing_adapter_cannot_start_more_provider_work(make_client):
    class DeadlineAdapter:
        async def invoke(self, assignment, runtime):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                async for _ in runtime.stream([], []):
                    pass
            return {"text": "late [S1]"}

    provider = Provider()
    client, _ = await make_client(
        provider,
        WorkflowBridge(),
        adapters=AdapterRegistry({"Report": DeadlineAdapter()}),
        run_timeout_seconds=0.1,
    )
    cid = await conversation(client)
    accepted = (
        await client.post(f"/v1/conversations/{cid}/messages", json=request("report"))
    ).json()
    final = await terminal(client, accepted["run_id"])
    assert not provider.inputs
    assert final["run"]["status"] == "failed"
    assert final["run"]["error"]["code"] == "timeout"


async def test_natural_route_does_not_feed_other_candidate_sources_to_adapter(workflow_client):
    class MixedBridge(WorkflowBridge):
        async def call(self, run_id, operation, arguments, capability, database):
            result = await super().call(run_id, operation, arguments, capability, database)
            if operation == "read_document":
                result["data"]["document"]["res_id"] = 22
            return result

    provider = Provider(
        [
            handoff("read_document", {"attachment_id": 31}),
            handoff("candidate_workflow", {"action": "report", "applicant_id": 21}),
            [{"type": "text.delta", "text": "The interview is incomplete. [S2]"}],
        ]
    )
    client, _ = await workflow_client(provider, MixedBridge())
    cid = await conversation(client)
    body = request("report")
    body.pop("workflow")
    accepted = (await client.post(f"/v1/conversations/{cid}/messages", json=body)).json()
    final = await terminal(client, accepted["run_id"])
    assert final["run"]["status"] == "completed", final
    assignment = json.loads(provider.inputs[-1][0][0]["content"])
    assert not [
        s for s in assignment["registry"] if s["model"] == "hr.applicant" and s["record_id"] == 22
    ]


async def test_prepare_direct_interview_routes_without_schedule_or_session_creation(
    workflow_client,
):
    class DirectBridge(WorkflowBridge):
        async def call(self, run_id, operation, arguments, capability, database):
            if operation == "workflow_prepare_direct":
                self.calls.append((operation, arguments, capability))
                self.case["direct_guide"] = {
                    "version": 1,
                    **{
                        k: v
                        for k, v in arguments["arguments"].items()
                        if k in {"questions", "language"}
                    },
                }
                return {"ok": True, "data": {"case": copy.deepcopy(self.case)}, "sources": []}
            return await super().call(run_id, operation, arguments, capability, database)

    questions = [
        {"criterion_id": "c1", "question": f"Your Python example {i + 1}?", "follow_up": None}
        for i in range(5)
    ]
    provider = Provider(
        [
            handoff("submit_guide", {"language": "en", "questions": questions}),
            [
                {
                    "type": "text.delta",
                    "text": "The typed guide is prepared; no answers submitted. [S1]",
                }
            ],
        ]
    )
    bridge = DirectBridge()
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    response = await client.post(
        f"/v1/conversations/{cid}/messages", json=request("prepare_interview")
    )
    assert response.status_code == 200, response.text
    result = await terminal(client, response.json()["run_id"])
    assert result["run"]["status"] == "completed", result
    assert [i[2] for i in provider.inputs] == ["Interview", "Report"]
    names = [c[0] for c in bridge.calls]
    assert "workflow_prepare_direct" in names
    assert not set(names) & {"workflow_schedule", "workflow_confirm", "workflow.interview.create"}
    assert bridge.case["direct_guide"]["questions"] == questions


async def test_streaming_tokens_do_not_fan_out_odoo_freshness_requests(workflow_client):
    class MeteredBridge(WorkflowBridge):
        validations = 0

        async def call(self, *args, **kwargs):
            if args[1] == "validate_sources" and args[3] == "signed-test-capability":
                self.validations += 1
            return await super().call(*args, **kwargs)

    bridge = MeteredBridge()
    provider = Provider(
        [
            [{"type": "text.delta", "text": "Evidence "} for _ in range(200)]
            + [{"type": "text.delta", "text": "[S1]"}]
        ]
    )
    client, _ = await workflow_client(provider, bridge)
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request("report"))
    result = await terminal(client, response.json()["run_id"])
    assert result["run"]["status"] == "completed", result
    # Streaming deltas remain private; validate before/after provider work and
    # delivery, not once per token (an Odoo round trip can take seconds).
    assert bridge.validations < 30, bridge.validations


async def test_sources_revoked_during_specialist_stream_block_final_delivery(workflow_client):
    bridge = WorkflowBridge()

    class RevokingProvider:
        async def stream(self, messages, tools, role):
            yield {"type": "text.delta", "text": "Private draft "}
            bridge.state = "unavailable"
            yield {"type": "text.delta", "text": "[S1]"}

    client, _ = await workflow_client(RevokingProvider(), bridge)
    cid = await conversation(client)
    response = await client.post(f"/v1/conversations/{cid}/messages", json=request("report"))
    result = await terminal(client, response.json()["run_id"])
    assert result["run"]["status"] == "needs_input"
    assert not [event for event in result["events"] if event["type"] == "message.completed"]
