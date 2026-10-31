import pytest
from app.report_agent import ReportRequest, ReportService
from pydantic import ValidationError


def request(branches):
    return ReportRequest.model_validate({
        "run_id": "run-1", "assignment": "Compile evidence", "candidate_id": "candidate-1",
        "role_id": "backend", "role_version": "v1", "branches": branches,
    })


def branch(name="Filter", **updates):
    value = {
        "name": name, "status": "Complete", "requested": True,
        "summary": "Evidence review completed.",
        "findings": [{"criterion_id": "java", "status": "supported", "source_ref": "S1"}],
        "evidence_gaps": [], "limitations": ["Candidate statements are self-reported."],
        "open_items": [], "next_action": None,
        "artifact_refs": [{"artifact_id": "filter-1", "source_state": "current"}],
    }
    value.update(updates)
    return value


def test_preserves_findings_and_artifacts():
    source = branch()
    response = ReportService().execute(request([source]))
    assert response.overall_status == "Complete"
    assert response.findings == source["findings"]
    assert response.artifact_refs == source["artifact_refs"]


@pytest.mark.parametrize("status", ["Needs input", "blocked", "cancelled", "outdated"])
def test_incomplete_branch_stays_visible(status):
    response = ReportService().execute(request([branch(
        status=status, evidence_gaps=[{"criterion_id": "profiling"}],
        open_items=["Candidate answer is required."],
        next_action={"action": "Collect the submitted answer.", "owner": "recruiter"},
    )]))
    assert response.report_status == "Complete"
    assert response.overall_status == "Needs input"
    assert response.branch_states[0].status == status
    assert response.next_actions[0].owner == "recruiter"


def test_failed_requested_branch_remains_failed():
    assert ReportService().execute(request([branch(status="Failed")])).overall_status == "Failed"


def test_not_requested_branch_is_preserved_without_failure():
    response = ReportService().execute(request([
        branch(), branch("Interview", requested=False, findings=[], limitations=[], artifact_refs=[]),
    ]))
    assert response.overall_status == "Complete"
    assert response.branch_states[1].requested is False


def test_no_requested_scope_needs_input():
    response = ReportService().execute(request([
        branch(requested=False, findings=[], limitations=[], artifact_refs=[]),
    ]))
    assert response.report_status == "Needs input"
    assert response.overall_status == "Needs input"


@pytest.mark.parametrize("field", ["run_id", "assignment", "candidate_id", "role_id", "role_version"])
def test_missing_identity_is_rejected(field):
    raw = {
        "run_id": "run-1", "assignment": "Compile", "candidate_id": "candidate-1",
        "role_id": "backend", "role_version": "v1", "branches": [branch()],
    }
    raw.pop(field)
    with pytest.raises(ValidationError):
        ReportRequest.model_validate(raw)


@pytest.mark.parametrize("field", ["score", "ranking", "recommendation", "decision", "suitability"])
def test_decision_fields_are_rejected(field):
    with pytest.raises(ValidationError, match="prohibited decision field"):
        request([branch(findings=[{"criterion_id": "c1", field: 99}])])
