"""Harness-compatible Report Agent adapted from commit d60edba.

The source Odoo addon compiled supplied specialist handoffs. In this repository
the runtime specialist belongs in the Python backend, so this module retains
that compiler behavior while enforcing the shared evidence/authority contract.
"""

import json
from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, model_validator

from .schemas import StrictModel

BranchStatus = Literal["Complete", "Needs input", "Failed", "blocked", "cancelled", "outdated"]
OverallStatus = Literal["Complete", "Needs input", "Failed"]
FORBIDDEN_KEYS = {
    "score", "scores", "rank", "ranking", "recommendation", "recommendations",
    "verdict", "decision", "suitability", "stage_change",
}


def _reject_decisions(value: Any, path: str = "bundle") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in FORBIDDEN_KEYS:
                raise ValueError(f"{path} contains prohibited decision field: {key}")
            _reject_decisions(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_decisions(child, f"{path}[{index}]")


class NextActionInput(StrictModel):
    action: str = Field(min_length=1, max_length=2000)
    owner: str = Field(min_length=1, max_length=200)


class BranchHandoff(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    status: BranchStatus
    requested: bool
    summary: str = Field(default="", max_length=5000)
    findings: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    evidence_gaps: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=100)
    open_items: list[str] = Field(default_factory=list, max_length=100)
    next_action: NextActionInput | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class ReportRequest(StrictModel):
    run_id: str = Field(min_length=1, max_length=200)
    assignment: str = Field(min_length=1, max_length=2000)
    candidate_id: str = Field(min_length=1, max_length=200)
    role_id: str = Field(min_length=1, max_length=200)
    role_version: str = Field(min_length=1, max_length=200)
    branches: list[BranchHandoff] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_bundle(self):
        names = [branch.name.casefold() for branch in self.branches]
        if len(names) != len(set(names)):
            raise ValueError("branch names must be unique")
        raw = self.model_dump(mode="json")
        _reject_decisions(raw)
        if len(json.dumps(raw, ensure_ascii=False)) > 64_000:
            raise ValueError("report request exceeds 64 KB")
        return self


class NextAction(StrictModel):
    branch: str
    action: str
    owner: str


class BranchState(StrictModel):
    name: str
    status: BranchStatus
    requested: bool
    summary: str = ""
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence_gaps: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    next_action: str = ""
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)


class ReportResponse(StrictModel):
    run_id: str
    report_status: Literal["Complete", "Needs input"]
    overall_status: OverallStatus
    summary: str
    branch_states: list[BranchState]
    findings: list[dict[str, Any]]
    evidence_gaps: list[dict[str, Any]]
    limitations: list[str]
    open_items: list[str]
    next_actions: list[NextAction]
    artifact_refs: list[dict[str, Any]]


class ReportService:
    """Compile validated handoffs without inventing evidence or decisions."""

    def execute(self, request: ReportRequest) -> ReportResponse:
        requested = [branch for branch in request.branches if branch.requested]
        missing_scope = not requested
        failed = any(branch.status == "Failed" for branch in requested)
        incomplete = any(
            branch.status in {"Needs input", "blocked", "cancelled", "outdated"}
            or branch.evidence_gaps or branch.open_items
            for branch in requested
        )
        overall: OverallStatus = (
            "Failed" if failed else "Needs input" if missing_scope or incomplete else "Complete"
        )
        findings = [deepcopy(item) for branch in request.branches for item in branch.findings]
        gaps = [deepcopy(item) for branch in request.branches for item in branch.evidence_gaps]
        limitations = list(dict.fromkeys(item for branch in request.branches for item in branch.limitations))
        open_items = list(dict.fromkeys(item for branch in request.branches for item in branch.open_items))
        artifacts = [deepcopy(item) for branch in request.branches for item in branch.artifact_refs]
        actions = [
            NextAction(branch=branch.name, action=branch.next_action.action, owner=branch.next_action.owner)
            for branch in request.branches if branch.next_action
        ]
        states = [BranchState(
            name=branch.name, status=branch.status, requested=branch.requested,
            summary=branch.summary, findings=deepcopy(branch.findings),
            evidence_gaps=deepcopy(branch.evidence_gaps), limitations=list(branch.limitations),
            open_items=list(branch.open_items),
            next_action=branch.next_action.action if branch.next_action else "",
            artifact_refs=deepcopy(branch.artifact_refs),
        ) for branch in request.branches]
        branch_text = ", ".join(f"{b.name}={b.status}" for b in request.branches)
        summary = (
            f"Report for {request.candidate_id}: overall status {overall}. "
            f"Branches: {branch_text}. Preserved {len(findings)} finding(s) and "
            f"{len(gaps)} evidence gap(s)."
        )
        return ReportResponse(
            run_id=request.run_id,
            report_status="Needs input" if missing_scope else "Complete",
            overall_status=overall,
            summary=summary,
            branch_states=states,
            findings=findings,
            evidence_gaps=gaps,
            limitations=limitations,
            open_items=open_items,
            next_actions=actions,
            artifact_refs=artifacts,
        )
