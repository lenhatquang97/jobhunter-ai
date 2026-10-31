"""Bounded specialist execution over the signed Odoo workflow boundary."""

import asyncio
import time
from uuid import uuid4

from .documents import extract_document_async
from .schemas import SafeError, SourceRef
from .store import ConversationCandidate, Run, RunWorkflow, now
from .workflow_schemas import Guide, ScheduleInput, Screening


class WorkflowRunner:
    def __init__(self, service, rid, body, requests=0, tool_calls=0, sources=None):
        self.service, self.rid, self.body = service, rid, body
        self.sources = list(sources or [])
        self.active_sources = []
        self.deadline = service.deadlines.get(
            rid, time.monotonic() + service.settings.run_timeout_seconds
        )
        self.calls, self.requests = tool_calls, requests
        self.case = None

    async def check(self, freshness=True):
        import asyncio

        async with self.service.store.session() as session:
            run = await session.get(Run, self.rid)
            if not run or run.status not in {"queued", "running"}:
                raise asyncio.CancelledError()
        if time.monotonic() >= self.deadline or (
            asyncio.current_task() and asyncio.current_task().cancelling()
        ):
            raise SafeError("timeout", "The run reached its time limit.")
        if freshness:
            await self.service.validated(self.rid, self.body.capability, self.body.odoo_database)

    async def register(self, raws):
        registered = []
        for raw in raws:
            raw = dict(raw)
            raw.pop("truncated", None)
            source = SourceRef.model_validate(raw).model_dump(exclude_none=True)
            identity = {k: v for k, v in source.items() if k != "key"}
            old = next(
                (s for s in self.sources if {k: v for k, v in s.items() if k != "key"} == identity),
                None,
            )
            if old is None:
                source["key"] = f"S{len(self.sources) + 1}"
                self.sources.append(source)
            else:
                source = old
            registered.append(source)
            if not any(s["key"] == source["key"] for s in self.active_sources):
                self.active_sources.append(source)
        if len(self.sources) > 200 or sum(len(s["excerpt"]) for s in self.sources) > 100000:
            raise SafeError("source_limit", "The retrieved evidence exceeded the run limit.")
        await self.service.save_sources(self.rid, self.sources)
        await self.check()
        return registered

    async def tool(self, name, args, agent):
        self.calls += 1
        if self.calls > self.service.settings.max_tool_calls:
            raise SafeError("tool_limit", "The run reached its tool call limit.")
        call_id = str(uuid4())
        # Public activity contains validated identifiers/inputs, never grants or raw handoff payloads.
        public_args = {
            k: v for k, v in args.items() if k not in {"content", "sources", "questions"}
        }
        if "schedule" in public_args:
            public_args["provided_fields"] = sorted(
                k for k, v in public_args.pop("schedule").items() if v is not None
            )
        await self.service.event(
            self.rid, "tool.started", {"name": name, "arguments": public_args}, agent, call_id
        )
        try:
            result = await self.service.bridge.call(
                self.rid,
                name,
                {"arguments": args, "context_refs": self.body.canonical_context()},
                self.body.capability,
                self.body.odoo_database,
            )
            if not result.get("ok"):
                code = result.get("error", {}).get("code", "tool_error")
                allowed = {
                    "access_denied",
                    "needs_input",
                    "source_outdated",
                    "conflict",
                    "invalid_arguments",
                    "unreadable_document",
                    "configuration_required",
                    "invalid_approval",
                }
                code = code if code in allowed else "tool_error"
                raise SafeError(
                    code,
                    "Odoo could not complete this workflow step. Refresh the candidate workflow and check the required inputs.",
                    needs_input=code in allowed - {"access_denied", "tool_error"},
                )
            raws = result.get("sources", [])
            if name == "read_document":
                if result.get("data", {}).get("document", {}).get("id") != args["attachment_id"]:
                    raise SafeError("invalid_tool_result")
                try:
                    raws = await extract_document_async(result["data"]["document"])
                except (ValueError, KeyError):
                    raise SafeError(
                        "unreadable_document",
                        "Select a readable PDF, DOCX or UTF-8 CV.",
                        needs_input=True,
                    ) from None
            registered = await self.register(raws)
            if "case" in result["data"]:
                case = result["data"]["case"]
                if case.get("applicant_id") != self.body.workflow.applicant_id:
                    raise SafeError(
                        "invalid_tool_result", "The workflow returned another candidate."
                    )
                self.case = case
                if name == "workflow_context":
                    for source in self.sources:
                        belongs = (
                            source["model"] == "hr.applicant"
                            and source["record_id"] == case["applicant_id"]
                        ) or (source["model"] == "hr.job" and source["record_id"] == case["job_id"])
                        if belongs and not any(
                            s["key"] == source["key"] for s in self.active_sources
                        ):
                            self.active_sources.append(source)
            await self.service.event(
                self.rid,
                "tool.completed",
                {"name": name, "summary": "Workflow step completed.", "sources": registered},
                agent,
                call_id,
            )
            return result["data"]
        except SafeError as exc:
            await self.service.event(
                self.rid,
                "tool.failed",
                {"name": name, "code": exc.code, "message": exc.message},
                agent,
                call_id,
            )
            raise

    async def role(self, name, model=None, function_name=None, extra=None):
        await self.check()
        # Missing plugins are explicit; no specialist activity is invented.
        if name not in self.service.adapters.adapters:
            raise SafeError(
                "adapter_unavailable",
                f"The {name} adapter is not configured. Plug in this specialist to continue.",
                needs_input=True,
            )
        await self.service.event(self.rid, "agent.started", {"name": name}, name)
        assignment = {
            "contract_version": "2.0",
            "role": name,
            "request": self.body.text,
            "case": self.case,
            "registry": self.active_sources,
            "mode": extra or "workflow",
            "handoff_name": function_name,
            "output_schema": model.model_json_schema()
            if model
            else {"type": "object", "properties": {"text": {"type": "string"}}},
        }
        await self.service.reset_draft(self.rid, name)
        runner = self

        class Runtime:
            async def stream(self, messages, tools, instructions=None):
                await runner.check()
                runner.requests += 1
                if runner.requests > runner.service.settings.max_provider_requests:
                    raise SafeError("request_limit", "The run reached its provider request limit.")
                kwargs = {"instructions": instructions} if instructions is not None else {}
                async with asyncio.timeout(max(0, runner.deadline - time.monotonic())):
                    async for event in runner.service.provider.stream(
                        messages, tools, name, **kwargs
                    ):
                        # Deltas remain private until final source validation.
                        # Keep cancellation/deadline checks per token without
                        # a network freshness round trip for every delta.
                        await runner.check(freshness=False)
                        yield event

        try:
            value = await self.service.adapters.invoke(name, assignment, Runtime())
            if model:
                try:
                    value = model.model_validate(value).model_dump(mode="json")
                except ValueError:
                    raise SafeError(
                        "invalid_handoff", "The specialist handoff did not match its contract."
                    ) from None
            else:
                from .service import validate_citations

                if set(value) != {"text"} or not isinstance(value["text"], str):
                    raise SafeError("invalid_handoff", "The specialist must return cited text.")
                value = value["text"]
                validate_citations(value, self.active_sources)
            await self.check()
            await self.service.event(self.rid, "agent.completed", {"name": name}, name)
            return value
        except Exception:
            await self.service.event(
                self.rid,
                "agent.failed",
                {"name": name, "message": "The specialist adapter did not complete."},
                name,
            )
            raise

    async def bind(self):
        async with self.service.lock, self.service.store.session() as session, session.begin():
            run = await session.get(Run, self.rid)
            if run.status not in {"queued", "running"}:
                import asyncio

                raise asyncio.CancelledError()
            entry = await session.get(
                ConversationCandidate, (run.conversation_id, self.case["applicant_id"])
            )
            if entry:
                entry.case_id, entry.focused_at = self.case["id"], now()
            else:
                session.add(
                    ConversationCandidate(
                        conversation_id=run.conversation_id,
                        applicant_id=self.case["applicant_id"],
                        case_id=self.case["id"],
                    )
                )
            saved = await session.get(RunWorkflow, self.rid)
            if not saved:
                session.add(
                    RunWorkflow(run_id=self.rid, request=self.body.workflow.model_dump(mode="json"))
                )
        await self.service.event(
            self.rid,
            "workflow.updated",
            {"case_id": self.case["id"], "applicant_id": self.case["applicant_id"]},
            "Orchestrator",
        )

    async def save(self, kind, content, agent):
        await self.check()
        await self.tool(
            "workflow_save",
            {
                "case_id": self.case["id"],
                "expected_version": self.case["version"],
                "kind": kind,
                "content": content,
                "sources": list(self.active_sources),
            },
            agent,
        )

    async def run(self, orchestrator_started=False):
        if not orchestrator_started:
            await self.service.event(
                self.rid, "agent.started", {"name": "Orchestrator"}, "Orchestrator"
            )
        await self.tool(
            "workflow_context", {"applicant_id": self.body.workflow.applicant_id}, "Orchestrator"
        )
        await self.bind()
        await self.service.event(
            self.rid, "agent.completed", {"name": "Orchestrator"}, "Orchestrator"
        )
        action = self.body.workflow.action
        if action == "orchestrate" and self.case.get("automation_state") in {
            "awaiting_candidate", "interview", "declined", "ghosted", "reporting", "completed"
        }:
            key = self.active_sources[-1]["key"] if self.active_sources else None
            message = f"This candidate workflow is already {self.case['automation_state']}; no duplicate reservation or invitation was created."
            if key:
                message += f" [{key}]"
            await self.service.publish_answer(
                self.rid, [message], self.active_sources, "Orchestrator"
            )
            return
        if action == "orchestrate":
            await self.tool(
                "workflow_automation_start",
                {"case_id": self.case["id"], "expected_version": self.case["version"]},
                "Orchestrator",
            )
        if action in {
            "screen", "schedule", "confirm", "prepare_interview"
        } and not self.case.get(
            "criteria_approved"
        ):
            raise SafeError(
                "criteria_approval_required",
                "Review and approve the current role criteria before continuing.",
                needs_input=True,
            )
        if action == "screen":
            documents = self.case.get("documents", [])
            if not documents:
                raise SafeError(
                    "missing_cv",
                    "Select an existing readable CV for this candidate.",
                    needs_input=True,
                )
            # Bound retrieval; never silently combine another applicant's documents.
            for doc in documents[:3]:
                await self.tool("read_document", {"attachment_id": doc["id"]}, "Orchestrator")
            review = await self.role("Filter", Screening, "submit_screening")
            criteria = {c["id"] for c in self.case["criteria"]}
            if (
                len(review["findings"]) != len(criteria)
                or {f["criterion_id"] for f in review["findings"]} != criteria
            ):
                raise SafeError(
                    "invalid_handoff", "The screening did not cover the approved criteria."
                )
            registry = {s["key"]: s["excerpt"] for s in self.active_sources}
            for finding in review["findings"]:
                if finding["status"] != "unknown" and not finding["citations"]:
                    raise SafeError("invalid_citation", "A screening finding has no evidence.")
                for cite in finding["citations"]:
                    if (
                        cite["source_key"] not in registry
                        or cite["quote"] not in registry[cite["source_key"]]
                    ):
                        raise SafeError(
                            "invalid_citation", "A screening quotation did not match its source."
                        )
            await self.save("screening", review, "Filter")
        elif action == "prepare_interview":
            for doc in self.case.get("documents", [])[:3]:
                await self.tool("read_document", {"attachment_id": doc["id"]}, "Orchestrator")
            guide = await self.role("Interview", Guide, "submit_guide", "guide")
            if any(
                q["criterion_id"] not in {c["id"] for c in self.case["criteria"]}
                for q in guide["questions"]
            ):
                raise SafeError("invalid_handoff", "Questions must use the approved criteria.")
            await self.tool(
                "workflow_prepare_direct",
                {"case_id": self.case["id"], "expected_version": self.case["version"], **guide},
                "Interview",
            )
        elif action == "schedule":
            schedule = await self.role("Schedule", ScheduleInput, "submit_schedule")
            supplied = self.body.workflow.data.get("schedule")
            if supplied is not None:
                # Explicit structured user inputs take precedence over model extraction.
                schedule.update(
                    ScheduleInput.model_validate(supplied).model_dump(
                        mode="json", exclude_none=True
                    )
                )
            await self.tool(
                "workflow_schedule",
                {
                    "case_id": self.case["id"],
                    "expected_version": self.case["version"],
                    "schedule": schedule,
                },
                "Schedule",
            )
            draft = self.case.get("schedule") or {}
            if draft.get("status") != "ready":
                missing = ", ".join(draft.get("missing", []) + draft.get("issues", []))
                raise SafeError(
                    "schedule_needs_input",
                    "Please provide or correct: " + (missing or "the scheduling details") + ".",
                    needs_input=True,
                )
            guide = await self.role("Interview", Guide, "submit_guide", "guide")
            if any(
                q["criterion_id"] not in {c["id"] for c in self.case["criteria"]}
                for q in guide["questions"]
            ):
                raise SafeError(
                    "invalid_handoff", "Interview questions must use approved criteria."
                )
            await self.tool(
                "workflow_prepare",
                {"case_id": self.case["id"], "expected_version": self.case["version"], **guide},
                "Interview",
            )
        elif action == "orchestrate":
            documents = self.case.get("documents", [])
            if not documents:
                raise SafeError(
                    "missing_cv",
                    "Select an existing readable CV for this candidate.",
                    needs_input=True,
                )
            for doc in documents[:3]:
                await self.tool("read_document", {"attachment_id": doc["id"]}, "Orchestrator")

            review = await self.role("Filter", Screening, "submit_screening")
            criteria = {c["id"] for c in self.case["criteria"]}
            if (
                len(review["findings"]) != len(criteria)
                or {f["criterion_id"] for f in review["findings"]} != criteria
            ):
                raise SafeError(
                    "invalid_handoff", "The screening did not cover the approved criteria."
                )
            registry = {s["key"]: s["excerpt"] for s in self.active_sources}
            for finding in review["findings"]:
                if finding["status"] != "unknown" and not finding["citations"]:
                    raise SafeError("invalid_citation", "A screening finding has no evidence.")
                for cite in finding["citations"]:
                    if (
                        cite["source_key"] not in registry
                        or cite["quote"] not in registry[cite["source_key"]]
                    ):
                        raise SafeError(
                            "invalid_citation", "A screening quotation did not match its source."
                        )
            await self.save("screening", review, "Filter")

            await self.service.event(
                self.rid, "agent.started", {"name": "Schedule"}, "Schedule"
            )
            await self.tool(
                "workflow_auto_schedule",
                {"case_id": self.case["id"], "expected_version": self.case["version"]},
                "Schedule",
            )
            await self.service.event(
                self.rid, "agent.completed", {"name": "Schedule"}, "Schedule"
            )
            draft = self.case.get("schedule") or {}
            if draft.get("status") != "ready":
                missing = ", ".join(draft.get("missing", []) + draft.get("issues", []))
                raise SafeError(
                    "schedule_needs_input",
                    "Automation paused at scheduling. Assign an interviewer and provide a candidate email, then retry: "
                    + (missing or "the scheduling details")
                    + ". The completed screening remains saved.",
                    needs_input=True,
                )

            guide = await self.role("Interview", Guide, "submit_guide", "guide")
            if any(q["criterion_id"] not in criteria for q in guide["questions"]):
                raise SafeError(
                    "invalid_handoff", "Interview questions must use approved criteria."
                )
            await self.tool(
                "workflow_prepare",
                {"case_id": self.case["id"], "expected_version": self.case["version"], **guide},
                "Interview",
            )
            await self.tool(
                "workflow_dispatch",
                {"case_id": self.case["id"], "expected_version": self.case["version"]},
                "Schedule",
            )
            await self.check()
            key = self.active_sources[-1]["key"] if self.active_sources else None
            message = "The CV evidence was reviewed, the earliest available interviewer slot was reserved, and one candidate invitation was queued. The workflow is now waiting for the candidate to accept or decline; no hiring stage or hiring decision was changed."
            if key:
                message += f" [{key}]"
            await self.service.publish_answer(
                self.rid,
                [message],
                self.active_sources,
                "Orchestrator",
            )
            return
        elif action == "confirm":
            await self.tool(
                "workflow_confirm",
                {
                    "case_id": self.case["id"],
                    "draft_version": self.body.workflow.data["draft_version"],
                },
                "Schedule",
            )
            await self.check()
            key = self.active_sources[-1]["key"] if self.active_sources else None
            text = (
                "The booking step completed. Check the saved reservation and invitation status in the candidate workflow."
                + (f" [{key}]" if key else "")
            )
            await self.service.publish_answer(self.rid, [text], self.active_sources, "Orchestrator")
            return
        elif action == "report":
            interview = self.case.get("interview") or {}
            if interview.get("submitted_count", 0):
                summary = await self.role(
                    "Interview",
                    extra="summarize actual submitted answers; incomplete sessions remain incomplete",
                )
                await self.save("interview_summary", {"text": summary}, "Interview")
        final = await self.role(
            "Report",
            extra="Summarize only actual available artifacts. If no submitted answers exist, explicitly label interview evaluation incomplete. Scheduling logistics are not evidence of ability. Never claim booked/sent unless saved booking/mail state confirms it.",
        )
        if action == "report":
            await self.save("report", {"text": final}, "Report")
        await self.check()
        await self.service.publish_answer(self.rid, [final], self.active_sources, "Report")
