import asyncio
import json
import re
import time
from contextlib import suppress

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .documents import extract_document_async
from .schemas import SafeError, SourceRef, WorkflowRequest
from .store import Conversation, ConversationCandidate, Event, Message, Run, RunWorkflow, now
from .tools import TOOL_DEFINITIONS, filter_data, validate_arguments

ACTIVE = {"queued", "running"}


class Service:
    def __init__(self, store, settings, provider, bridge, adapters=None):
        from .adapters import AdapterRegistry

        self.adapters = adapters if adapters is not None else AdapterRegistry()
        self.store, self.settings, self.provider, self.bridge = store, settings, provider, bridge
        self.lock = asyncio.Lock()
        self.tasks = {}
        self.deadlines = {}
        self.stopping = False

    async def initialize(self):
        await self.store.initialize()
        async with self.lock, self.store.session() as session, session.begin():
            runs = (await session.scalars(select(Run).where(Run.status.in_(ACTIVE)))).all()
            for run in runs:
                run.status, run.finished_at = "interrupted", now()
                await self.add_event(session, run.id, "run.interrupted", {"status": "interrupted"})

    async def close(self):
        self.stopping = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for resource in (self.provider, self.bridge, self.adapters):
            if hasattr(resource, "close"):
                await resource.close()
        await self.store.close()

    async def owned(self, session, cid, principal):
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == str(cid), Conversation.owner_key == principal.owner_key
            )
        )
        if not conversation:
            raise HTTPException(404, "Conversation not found")
        return conversation

    async def owned_run(self, session, rid, principal):
        run = await session.scalar(
            select(Run)
            .join(Conversation)
            .where(Run.id == str(rid), Conversation.owner_key == principal.owner_key)
        )
        if not run:
            raise HTTPException(404, "Run not found")
        return run

    async def add_event(self, session, rid, kind, payload, agent=None, call_id=None):
        seq = (
            await session.scalar(select(func.max(Event.seq)).where(Event.run_id == rid)) or 0
        ) + 1
        session.add(
            Event(
                run_id=rid,
                seq=seq,
                type=kind,
                payload=payload,
                agent_id=agent,
                tool_call_id=call_id,
            )
        )
        await session.flush()

    async def event(self, rid, kind, payload, agent=None, call_id=None):
        async with self.lock, self.store.session() as session, session.begin():
            run = await session.get(Run, rid)
            if not run or run.status not in ACTIVE:
                raise asyncio.CancelledError()
            await self.add_event(session, rid, kind, payload, agent, call_id)

    async def create_conversation(self, principal):
        async with self.lock, self.store.session() as session, session.begin():
            item = Conversation(owner_key=principal.owner_key)
            session.add(item)
            await session.flush()
            return item.public()

    async def list_conversations(self, principal):
        async with self.store.session() as session:
            rows = await session.scalars(
                select(Conversation)
                .where(
                    Conversation.owner_key == principal.owner_key, Conversation.archived.is_(False)
                )
                .order_by(Conversation.updated_at.desc())
                .limit(100)
            )
            return {"conversations": [row.public() for row in rows]}

    async def archive(self, cid, principal, archived):
        async with self.lock, self.store.session() as session, session.begin():
            row = await self.owned(session, cid, principal)
            row.archived, row.updated_at = archived, now()
            return row.public()

    async def source_state(self, run, capability, database, validation_run_id=None):
        if not run.sources:
            return "current"
        if not capability or not database:
            return "unavailable"
        try:
            async with asyncio.timeout(self.settings.bridge_timeout_seconds):
                result = await self.bridge.call(
                    str(validation_run_id or run.id),
                    "validate_sources",
                    {"sources": run.sources},
                    capability,
                    database,
                )
            if not result.get("ok"):
                return "unavailable"
            states = result["data"]["states"]
            if not isinstance(states, list) or len(states) != len(run.sources):
                return "unavailable"
            mapping = {entry["key"]: entry["state"] for entry in states}
            if len(mapping) != len(run.sources) or set(mapping) != {s["key"] for s in run.sources}:
                return "unavailable"
            if any(
                state not in {"current", "outdated", "unavailable"} for state in mapping.values()
            ):
                return "unavailable"
            if "unavailable" in mapping.values():
                return "unavailable"
            return "outdated" if "outdated" in mapping.values() else "current"
        except Exception:
            return "unavailable"

    async def history(self, cid, principal, capability, database, validation_run_id=None):
        async with self.lock, self.store.session() as session:
            conversation = await self.owned(session, cid, principal)
            runs = list(
                await session.scalars(
                    select(Run)
                    .where(Run.conversation_id == str(cid))
                    .order_by(Run.created_at.desc())
                    .limit(200)
                )
            )
            messages = list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == str(cid))
                    .where((Message.role == "user") | Message.validated.is_(True))
                    .order_by(Message.created_at.desc(), Message.id)
                    .limit(400)
                )
            )
            candidates = list(
                await session.scalars(
                    select(ConversationCandidate)
                    .where(ConversationCandidate.conversation_id == str(cid))
                    .order_by(ConversationCandidate.focused_at.desc())
                )
            )
        states = {run.id: "unavailable" if run.sources else "current" for run in runs}
        # One bounded retrieval budget, not N independent network timeout periods.
        with suppress(TimeoutError):
            async with asyncio.timeout(self.settings.bridge_timeout_seconds):
                for run in runs:
                    states[run.id] = await self.source_state(
                        run, capability, database, validation_run_id
                    )
        invalid = {r.id for r in runs if r.error and r.error.get("code") == "invalid_citation"}
        return {
            "conversation": conversation.public(),
            "messages": [
                m.public(
                    "unavailable" if m.run_id in invalid else states.get(m.run_id, "unavailable")
                )
                for m in reversed(messages)
            ],
            "runs": [run.public(states[run.id]) for run in reversed(runs)],
            "candidate_ids": sorted(c.applicant_id for c in candidates),
            "focused_applicant_id": candidates[0].applicant_id if candidates else None,
        }

    async def events(self, rid, principal, capability, database, after, validation_run_id=None):
        async with self.lock, self.store.session() as session:
            run = await self.owned_run(session, rid, principal)
            rows = list(
                await session.scalars(
                    select(Event)
                    .where(Event.run_id == str(rid), Event.seq > after)
                    .order_by(Event.seq)
                    .limit(self.settings.event_page_size)
                )
            )
        state = await self.source_state(run, capability, database, validation_run_id)
        return {
            "run": run.public(state),
            "events": [
                e.public(
                    state == "unavailable"
                    or bool(run.error and run.error.get("code") == "invalid_citation")
                )
                for e in rows
            ],
            "next_seq": rows[-1].seq if rows else after,
        }

    async def submit(self, cid, principal, body):
        rid, message_id = str(body.run_id), str(body.client_message_id)
        async with self.lock:
            try:
                async with self.store.session() as session, session.begin():
                    conversation = await self.owned(session, cid, principal)
                    existing = await session.scalar(
                        select(Run).where(
                            Run.conversation_id == str(cid), Run.client_message_id == message_id
                        )
                    )
                    if existing:
                        if existing.fingerprint != body.fingerprint():
                            raise HTTPException(409, "Message ID already has different content")
                        message = await session.scalar(
                            select(Message).where(
                                Message.run_id == existing.id, Message.role == "user"
                            )
                        )
                        return {
                            "run_id": existing.id,
                            "message_id": message.id,
                            "status": existing.status,
                        }
                    if conversation.archived:
                        raise HTTPException(409, "Conversation is archived")
                    if await session.get(Run, rid) or await session.scalar(
                        select(Run.id).where(
                            Run.conversation_id == str(cid), Run.status.in_(ACTIVE)
                        )
                    ):
                        raise HTTPException(409, "Run conflicts with an existing run")
                    if body.retry_of_run_id:
                        retry = await self.owned_run(session, body.retry_of_run_id, principal)
                        if retry.conversation_id != str(cid):
                            raise HTTPException(404, "Run not found")
                        if retry.status in ACTIVE:
                            raise HTTPException(409, "Original run is active")
                    run = Run(
                        id=rid,
                        conversation_id=str(cid),
                        client_message_id=message_id,
                        fingerprint=body.fingerprint(),
                        retry_of_run_id=str(body.retry_of_run_id) if body.retry_of_run_id else None,
                    )
                    session.add(run)
                    await session.flush()
                    if body.workflow:
                        session.add(
                            RunWorkflow(run_id=rid, request=body.workflow.model_dump(mode="json"))
                        )
                    first_message = not await session.scalar(
                        select(Message.id).where(Message.conversation_id == str(cid)).limit(1)
                    )
                    message = Message(
                        conversation_id=str(cid),
                        role="user",
                        text=body.text,
                        run_id=rid,
                        context_refs=[
                            ref.model_dump(exclude_none=True) for ref in body.context_refs
                        ],
                    )
                    session.add(message)
                    conversation.updated_at = now()
                    # A private title comes only from its owner's first input, never model/source text.
                    if first_message:
                        conversation.title = " ".join(body.text.strip().splitlines()[0].split())[
                            :80
                        ]
                    await self.add_event(session, rid, "run.queued", {"status": "queued"})
                    await session.flush()
                    result = {"run_id": rid, "message_id": message.id, "status": "queued"}
            except IntegrityError as exc:
                raise HTTPException(409, "Run conflicts with an existing run") from exc
            task = asyncio.create_task(self.execute(rid, principal, body), name="jobhunter-run")
            self.tasks[rid] = task
            task.add_done_callback(lambda done: self.tasks.pop(rid, None))
            return result

    async def cancel(self, rid, principal):
        rid = str(rid)
        # Scope check precedes accessing the task registry.
        async with self.store.session() as session:
            run = await self.owned_run(session, rid, principal)
            if run.status not in ACTIVE:
                return {"run_id": rid, "status": run.status}
        # The durable terminal transition wins even if a provider ignores cancellation.
        await self.finish(rid, "cancelled")
        task = self.tasks.get(rid)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        async with self.store.session() as session:
            run = await self.owned_run(session, rid, principal)
            return {"run_id": rid, "status": run.status}

    async def finish(self, rid, status, error=None):
        async with self.lock, self.store.session() as session, session.begin():
            run = await session.get(Run, rid)
            if run.status not in ACTIVE:
                return
            run.status, run.finished_at, run.error = status, now(), error
            await self.add_event(
                session,
                rid,
                "run." + status,
                {"status": status, **({"error": error} if error else {})},
            )
            conversation = await session.get(Conversation, run.conversation_id)
            conversation.updated_at = now()

    async def reset_draft(self, rid, agent):
        async with self.lock, self.store.session() as session, session.begin():
            run = await session.get(Run, rid)
            if run.status not in ACTIVE:
                raise asyncio.CancelledError()
            run.draft_text, run.draft_agent = "", agent

    async def stage_text(self, rid, chunk):
        if not chunk:
            return
        async with self.lock, self.store.session() as session, session.begin():
            run = await session.get(Run, rid)
            if run.status not in ACTIVE:
                raise asyncio.CancelledError()
            if len(run.draft_text) + len(chunk) > 64000:
                raise SafeError("output_limit", "The response exceeded the output limit.")
            run.draft_text += chunk

    async def publish_answer(self, rid, chunks, sources, agent):
        answer = "".join(chunks)
        if not answer.strip():
            raise SafeError("empty_response", "The provider returned no answer.")
        if sources:
            validate_citations(answer, sources)
        elif re.search(r"\[S\d+\]", answer):
            raise SafeError(
                "invalid_citation", "The answer referenced a source that was not retrieved."
            )
        async with self.lock, self.store.session() as session, session.begin():
            run = await session.get(Run, rid)
            if run.status not in ACTIVE:
                raise asyncio.CancelledError()
            message = Message(
                run_id=rid,
                conversation_id=run.conversation_id,
                role="assistant",
                text=answer,
                sources=sources,
                validated=True,
            )
            session.add(message)
            await session.flush()
            for chunk in chunks:
                await self.add_event(session, rid, "message.delta", {"text": chunk}, agent)
            await self.add_event(
                session,
                rid,
                "message.completed",
                {"message_id": message.id, "text": message.text, "sources": sources},
                agent,
            )
            run.status, run.finished_at = "completed", now()
            await self.add_event(session, rid, "run.completed", {"status": "completed"})
            conversation = await session.get(Conversation, run.conversation_id)
            conversation.updated_at = now()

    async def save_sources(self, rid, sources):
        async with self.lock, self.store.session() as session, session.begin():
            run = await session.get(Run, rid)
            if run.status not in ACTIVE:
                raise asyncio.CancelledError()
            run.sources = list(sources)

    async def validated(self, rid, capability, database):
        async with self.store.session() as session:
            run = await session.get(Run, rid)
        state = await self.source_state(run, capability, database)
        if state != "current":
            async with self.lock, self.store.session() as session, session.begin():
                current = await session.get(Run, rid)
                if current.status not in ACTIVE:
                    raise asyncio.CancelledError()
                current.source_state = state
                await self.add_event(session, rid, "source.invalidated", {"state": state})
            raise SafeError(
                "source_" + state,
                "Source access or content changed. Select current sources and try again.",
                needs_input=True,
            )

    async def execute(self, rid, principal, body):
        self.deadlines[rid] = time.monotonic() + self.settings.run_timeout_seconds
        try:
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                await self._execute(rid, principal, body)
        except asyncio.CancelledError:
            await self.finish(rid, "interrupted" if self.stopping else "cancelled")
            raise
        except TimeoutError:
            await self.finish(
                rid, "failed", {"code": "timeout", "message": "The run reached its time limit."}
            )
        except SafeError as exc:
            await self.finish(
                rid,
                "needs_input" if exc.needs_input else "failed",
                {"code": exc.code, "message": exc.message},
            )
        except Exception:
            # No raw exception strings, provider payloads, source contents or credentials in logs/events.
            await self.finish(
                rid,
                "failed",
                {"code": "provider_error", "message": "The response could not be completed."},
            )
        finally:
            self.deadlines.pop(rid, None)

    async def _execute(self, rid, principal, body):
        async with self.lock, self.store.session() as session, session.begin():
            run = await session.get(Run, rid)
            if not run or run.status not in ACTIVE:
                raise asyncio.CancelledError()
            run.status, run.started_at = "running", now()
            await self.add_event(session, rid, "run.started", {"status": "running"})
        if not body.capability or not body.odoo_database:
            raise SafeError(
                "missing_context", "Refresh the Odoo session and try again.", needs_input=True
            )
        if body.workflow:
            from .workflows import WorkflowRunner

            await WorkflowRunner(self, rid, body).run()
            return
        history = await self.history(
            run.conversation_id, principal, body.capability, body.odoo_database, rid
        )
        messages = []
        sources = []
        total_chars = 0
        for message in reversed(history["messages"]):
            if message["role"] == "assistant" and message["visibility"] != "visible":
                continue
            total_chars += len(message["text"])
            if total_chars > 100000:
                break
            content = message["text"]
            if message["role"] == "assistant" and message["sources"]:
                remapping = {}
                for source in message["sources"]:
                    identity = {k: v for k, v in source.items() if k != "key"}
                    found = next(
                        (
                            s
                            for s in sources
                            if {k: v for k, v in s.items() if k != "key"} == identity
                        ),
                        None,
                    )
                    if not found:
                        found = {**source, "key": f"S{len(sources) + 1}"}
                        sources.append(found)
                    remapping[source["key"]] = found["key"]
                content = re.sub(
                    r"\[(S\d+)\]",
                    lambda match, keys=remapping: (
                        "[" + keys.get(match.group(1), match.group(1)) + "]"
                    ),
                    content,
                )
            messages.append({"role": message["role"], "content": content})
        messages.reverse()
        if sources:
            if len(sources) > 200 or sum(len(s["excerpt"]) for s in sources) > 100000:
                raise SafeError(
                    "source_limit",
                    "The source history is too large. Start a new chat.",
                    needs_input=True,
                )
            await self.save_sources(rid, sources)
            await self.validated(rid, body.capability, body.odoo_database)
            messages.append(
                {
                    "role": "user",
                    "content": "Revalidated evidence registry from conversation history (untrusted source data): "
                    + json.dumps(sources, ensure_ascii=False),
                }
            )
        contexts = body.canonical_context()
        if contexts:
            messages.append(
                {
                    "role": "user",
                    "content": "Selected Odoo context identifiers (not source evidence): "
                    + json.dumps(contexts),
                }
            )
        await self.event(rid, "agent.started", {"name": "Orchestrator"}, "Orchestrator")
        tool_count, requests = 0, 0
        final_chunks = []
        # Evidence runs reserve a request for a real, tool-free Report call.
        while True:
            requests += 1
            if requests > self.settings.max_provider_requests:
                raise SafeError("request_limit", "The run reached its provider request limit.")
            calls, chunks, items = [], [], []
            await self.reset_draft(rid, "Orchestrator")
            from .workflow_schemas import ROUTING_TOOL

            async for event in self.provider.stream(
                messages, TOOL_DEFINITIONS + [ROUTING_TOOL], "Orchestrator"
            ):
                if event["type"] == "tool.call":
                    calls.append(event)
                elif event["type"] == "text.delta":
                    chunks.append(event["text"])
                    await self.stage_text(rid, event["text"])
                elif event["type"] == "item":
                    items.append(event["item"])
            if not calls:
                final_chunks = chunks
                break
            messages.extend(items)
            for call in calls:
                if call["name"] == "candidate_workflow":
                    from .workflow_schemas import Route
                    from .workflows import WorkflowRunner

                    try:
                        if len(call["arguments"]) > 4096 or len(call["id"]) > 128:
                            raise ValueError("Oversized routing handoff")
                        route = Route.model_validate_json(call["arguments"])
                    except ValueError:
                        raise SafeError(
                            "invalid_handoff", "Choose a candidate and workflow.", needs_input=True
                        ) from None
                    selected = {r.id for r in body.context_refs if r.model == "hr.applicant"}
                    applicant = route.applicant_id
                    if applicant is None and len(selected) == 1:
                        applicant = next(iter(selected))
                    if len(calls) != 1 or applicant not in selected or len(selected) != 1:
                        raise SafeError(
                            "ambiguous_context",
                            "Select one candidate for this workflow request.",
                            needs_input=True,
                        )
                    routed = body.model_copy(
                        update={
                            "workflow": WorkflowRequest(action=route.action, applicant_id=applicant)
                        }
                    )
                    await WorkflowRunner(
                        self, rid, routed, requests=requests, tool_calls=tool_count, sources=sources
                    ).run(orchestrator_started=True)
                    return
                tool_count += 1
                if tool_count > self.settings.max_tool_calls:
                    raise SafeError("tool_limit", "The run reached its tool call limit.")
                if len(call["arguments"]) > 4096 or len(call["id"]) > 128:
                    raise SafeError("invalid_tool_call")
                try:
                    args = validate_arguments(call["name"], json.loads(call["arguments"]))
                except (ValueError, TypeError):
                    raise SafeError(
                        "invalid_tool_call", "The provider requested an invalid read operation."
                    ) from None
                name, call_id = call["name"], call["id"]
                if not any(
                    item.get("type") == "function_call" and item.get("call_id") == call_id
                    for item in items
                ):
                    messages.append(
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": json.dumps(args),
                        }
                    )
                await self.event(
                    rid, "tool.started", {"name": name, "arguments": {} if name == "create_job" else args}, "Orchestrator", call_id
                )
                try:
                    result = await self.bridge.call(
                        rid,
                        name,
                        {"arguments": args, "context_refs": contexts},
                        body.capability,
                        body.odoo_database,
                    )
                    if not result.get("ok"):
                        code = result.get("error", {}).get("code", "tool_error")
                        allowed = {
                            "access_denied",
                            "needs_input",
                            "unsupported_document",
                            "document_too_large",
                            "unreadable_document",
                            "ambiguous_context",
                        }
                        code = code if code in allowed else "tool_error"
                        raise SafeError(
                            code,
                            "Odoo could not provide the requested information.",
                            needs_input=code != "access_denied" and code != "tool_error",
                        )
                    raw_sources = result.get("sources", [])
                    if name == "read_document":
                        if (
                            result.get("data", {}).get("document", {}).get("id")
                            != args["attachment_id"]
                        ):
                            raise SafeError("invalid_tool_result")
                        try:
                            raw_sources = await extract_document_async(result["data"]["document"])
                        except (ValueError, KeyError):
                            raise SafeError(
                                "unreadable_document",
                                "Select a readable PDF, DOCX or UTF-8 text document.",
                                needs_input=True,
                            ) from None
                        data = {
                            "document": {
                                "id": result["data"]["document"]["id"],
                                "truncated": any(s.get("truncated") for s in raw_sources),
                            }
                        }
                    else:
                        data = filter_data(name, result["data"])
                        if name != "create_job" and any(data.values()) and not raw_sources:
                            raise SafeError(
                                "missing_sources",
                                "Odoo did not provide verifiable source references.",
                            )
                    registered = []
                    for raw in raw_sources:
                        raw = dict(raw)
                        raw.pop("truncated", None)
                        source = SourceRef.model_validate(raw).model_dump(exclude_none=True)
                        identity = {k: v for k, v in source.items() if k != "key"}
                        old = next(
                            (
                                s
                                for s in sources
                                if {k: v for k, v in s.items() if k != "key"} == identity
                            ),
                            None,
                        )
                        if old:
                            source = old
                        else:
                            source["key"] = f"S{len(sources) + 1}"
                            sources.append(source)
                        registered.append(source)
                    if len(sources) > 200 or sum(len(s["excerpt"]) for s in sources) > 100000:
                        raise SafeError(
                            "source_limit", "The retrieved evidence exceeded the run limit."
                        )
                    await self.save_sources(rid, sources)
                    # Nothing source-derived is published or reused until Odoo verifies it.
                    await self.validated(rid, body.capability, body.odoo_database)
                    await self.event(
                        rid,
                        "tool.completed",
                        {"name": name, "summary": "Job proposal prepared; recruiter confirmation required." if name == "create_job" else "Read completed.", "sources": registered},
                        "Orchestrator",
                        call_id,
                    )
                    messages.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(
                                {"data": data, "sources": registered}, ensure_ascii=False
                            ),
                        }
                    )
                except SafeError as exc:
                    await self.event(
                        rid,
                        "tool.failed",
                        {"name": name, "code": exc.code, "message": exc.message},
                        "Orchestrator",
                        call_id,
                    )
                    raise
                except Exception:
                    await self.event(
                        rid,
                        "tool.failed",
                        {
                            "name": name,
                            "code": "tool_error",
                            "message": "The read operation could not be completed.",
                        },
                        "Orchestrator",
                        call_id,
                    )
                    raise SafeError(
                        "tool_error", "The read operation could not be completed."
                    ) from None
        await self.event(rid, "agent.completed", {"name": "Orchestrator"}, "Orchestrator")
        if sources:
            await self.validated(rid, body.capability, body.odoo_database)
            if requests >= self.settings.max_provider_requests:
                raise SafeError("request_limit", "The run reached its provider request limit.")
            await self.event(rid, "agent.started", {"name": "Report"}, "Report")
            report_input = messages + [
                {
                    "role": "user",
                    "content": "Summarize the retrieved evidence using this verified registry: "
                    + json.dumps(sources, ensure_ascii=False),
                }
            ]
            final_chunks = []
            await self.reset_draft(rid, "Report")
            async for event in self.provider.stream(report_input, [], "Report"):
                if event["type"] == "tool.call":
                    raise SafeError("invalid_tool_call", "Report cannot execute tools.")
                if event["type"] == "text.delta":
                    final_chunks.append(event["text"])
                    await self.stage_text(rid, event["text"])
            text_result = "".join(final_chunks)
            validate_citations(text_result, sources)
            await self.validated(rid, body.capability, body.odoo_database)
            await self.event(rid, "agent.completed", {"name": "Report"}, "Report")
        elif contexts:
            # Context identifiers alone are never evidence.
            if not final_chunks:
                raise SafeError(
                    "missing_context",
                    "Select a readable source or clarify your request.",
                    needs_input=True,
                )
        await self.publish_answer(
            rid, final_chunks, sources, "Report" if sources else "Orchestrator"
        )


def validate_citations(answer, sources):
    registry = {source["key"]: source["excerpt"] for source in sources}
    cited = re.findall(r"\[(S\d+)\]", answer)
    if not cited or any(key not in registry for key in cited):
        raise SafeError(
            "invalid_citation", "The generated answer could not be matched to its sources."
        )
    # Quotes must match an immediately attached citation, not an unrelated later source.
    for match in re.finditer(r'["“]([^"”\n]+)["”]', answer):
        following = answer[match.end() :]
        attached = re.match(r"[\s,;:.!?]*(?:\[S\d+\][\s,;]*)+", following)
        keys = re.findall(r"\[(S\d+)\]", attached.group() if attached else "")
        if not keys or not any(match.group(1) in registry.get(key, "") for key in keys):
            raise SafeError(
                "invalid_citation", "A generated quotation did not match the cited evidence."
            )
