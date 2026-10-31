"""Strict specialist handoffs; Odoo remains the authority for all business writes."""

from typing import Literal

from pydantic import Field, StrictInt

from .schemas import StrictModel


class Citation(StrictModel):
    source_key: str = Field(max_length=128)
    quote: str = Field(min_length=1, max_length=3000)


class Finding(StrictModel):
    criterion_id: str = Field(max_length=100)
    status: Literal["supported", "partial", "unknown", "conflicting"]
    finding: str = Field(max_length=3000)
    citations: list[Citation] = Field(max_length=10)
    question: str = Field(max_length=1000)


class Screening(StrictModel):
    summary: str = Field(max_length=5000)
    findings: list[Finding] = Field(min_length=1, max_length=20)


class Window(StrictModel):
    start: str = Field(max_length=64)
    end: str = Field(max_length=64)


class Participant(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=254)
    timezone: str = Field(max_length=100)
    availability: list[Window] = Field(max_length=20)
    user_id: StrictInt | None = Field(default=None, gt=1)


class ScheduleInput(StrictModel):
    start: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=100)
    duration_minutes: StrictInt | None = Field(default=None, ge=15, le=180)
    participant_count: StrictInt | None = Field(default=None, ge=1, le=10)
    participants: list[Participant] | None = Field(default=None, max_length=10)
    candidate_email: str | None = Field(default=None, max_length=254)


class Question(StrictModel):
    criterion_id: str = Field(max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    follow_up: str | None = Field(default=None, max_length=2000)


class Guide(StrictModel):
    language: Literal["en", "vi"]
    questions: list[Question] = Field(min_length=5, max_length=5)


class Route(StrictModel):
    action: Literal["screen", "schedule", "report", "prepare_interview", "orchestrate"]
    applicant_id: StrictInt | None = Field(gt=0)


def function(name, model, description):
    schema = model.model_json_schema()

    def strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for child in node.values():
                strict(child)
        elif isinstance(node, list):
            for child in node:
                strict(child)

    strict(schema)
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": schema,
        "strict": True,
    }


ROUTING_TOOL = function(
    "candidate_workflow",
    Route,
    "Route an explicitly selected applicant to CV screening, direct typed interview preparation, scheduling, an evidence report, or the bounded full preparation flow. Never guess applicant identity. This cannot approve criteria, book or send mail.",
)
