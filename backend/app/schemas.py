import base64
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Principal(StrictModel):
    database_uuid: str = Field(min_length=1, max_length=128)
    user_id: StrictInt = Field(gt=1)
    company_ids: list[StrictInt] = Field(min_length=1, max_length=100)

    @field_validator("company_ids")
    @classmethod
    def normalize(cls, value):
        if any(i <= 0 for i in value):
            raise ValueError("Invalid companies")
        return sorted(set(value))

    @property
    def owner_key(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @classmethod
    def from_header(cls, value):
        if len(value) > 4096:
            raise ValueError("Invalid principal")
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        return cls.model_validate(json.loads(decoded))


class ContextRef(StrictModel):
    model: Literal["hr.job", "hr.applicant", "ir.attachment"]
    id: StrictInt = Field(gt=0)
    label: str | None = Field(default=None, max_length=200)


class WorkflowRequest(StrictModel):
    action: Literal[
        "screen", "schedule", "report", "confirm", "prepare_interview", "orchestrate"
    ]
    applicant_id: StrictInt = Field(gt=0)
    data: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_data(self):
        if len(json.dumps(self.data)) > 16000:
            raise ValueError("Workflow data too large")
        permitted = {
            "screen": set(),
            "prepare_interview": set(),
            "report": set(),
            "schedule": {"schedule"},
            "orchestrate": {"schedule"},
            "confirm": {"draft_version"},
        }
        if set(self.data) - permitted[self.action]:
            raise ValueError("Unknown workflow fields")
        if self.action == "confirm" and (
            set(self.data) != {"draft_version"}
            or type(self.data["draft_version"]) is not int
            or self.data["draft_version"] <= 0
        ):
            raise ValueError("Exact draft version required")
        if self.action in ("schedule", "orchestrate") and "schedule" in self.data:
            from .workflow_schemas import ScheduleInput

            ScheduleInput.model_validate(self.data["schedule"])
        return self


class Submit(StrictModel):
    run_id: UUID
    client_message_id: UUID
    text: str = Field(min_length=1, max_length=16000)
    context_refs: list[ContextRef] = Field(max_length=20)
    capability: str = Field(max_length=8192)
    odoo_database: str = Field(max_length=128)
    retry_of_run_id: UUID | None = None
    workflow: WorkflowRequest | None = None

    @model_validator(mode="after")
    def selected_workflow(self):
        if self.workflow and self.workflow.applicant_id not in {
            r.id for r in self.context_refs if r.model == "hr.applicant"
        }:
            raise ValueError("Select the workflow applicant explicitly")
        return self

    @field_validator("text")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value

    def fingerprint(self):
        # Grants may be refreshed on a replay; they are not logical message content.
        body = self.model_dump(mode="json", exclude={"capability", "run_id", "workflow"})
        if self.workflow is not None:
            body["workflow"] = self.workflow.model_dump(mode="json")
        body["context_refs"] = self.canonical_context()
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def canonical_context(self):
        return [
            {"model": model, "id": record_id}
            for model, record_id in sorted({(ref.model, ref.id) for ref in self.context_refs})
        ]


class Archive(StrictModel):
    archived: bool


class SourceRef(StrictModel):
    key: str = Field(min_length=1, max_length=128)
    model: Literal[
        "hr.job",
        "hr.applicant",
        "ir.attachment",
        "jobhunter.chat.case",
        "jobhunter.chat.artifact",
        "jobhunter.chat.answer",
    ]
    record_id: StrictInt = Field(gt=0)
    field: str | None = Field(default=None, max_length=100)
    attachment_id: StrictInt | None = Field(default=None, gt=0)
    title: str = Field(max_length=300)
    write_date: str = Field(min_length=1, max_length=64)
    checksum: str | None = Field(default=None, max_length=128)
    locator: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(max_length=10000)


class SafeError(Exception):
    def __init__(self, code, message="The request could not be completed.", needs_input=False):
        self.code = code
        self.message = message
        self.needs_input = needs_input
        super().__init__(code)
