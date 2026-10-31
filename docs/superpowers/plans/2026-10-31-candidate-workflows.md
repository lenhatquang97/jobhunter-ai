# Candidate Workflows Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development; do not commit automatically.

**Goal:** Native, evidence-based candidate workflows with runnable Playwright verification.
**Architecture:** Odoo owns private applicant/job cases and versioned artifacts; Python executes bounded actual specialist calls and persists private chat/run state. Native OWL consumes the existing BFF and workflow summaries; candidate typed page is session-scoped.
**Tech Stack:** Odoo20.0-20261015, Python3.12/FastAPI/Pydantic/SQLAlchemy/PostgreSQL/OpenAI Responses, OWL, pytest-playwright, local SMTP capture.
**Spec:** docs/superpowers/specs/2026-10-31-candidate-workflows-design.md

## Global Constraints
- No scores/rankings/stage changes/legacy automation; only explicitly confirmed workflow artifacts/calendar reservation/email writes.
- Same recruiter/database/fixed company context; no candidate identity merge; exact citations and current access/version rechecks.
- Run limits120s/6provider/8tools; no silent retries; no secrets/raw candidate data in evidence.
- Multiple candidates per chat and multiple chats per candidate. Live invitation pending publicHTTPS/SMTP; no commits/push/deploy.

## Review Focus
- Lost acknowledgments must not duplicate bookings, mail, answers or user submissions.
- Cross-candidate/cross-user/company context must not bleed; ambiguous identities require selection.
- Source changes invalidate confirmations and derived results; revoked access hides dependent artifacts.
- DST gaps/folds, missing participant details, no overlap and calendar races must fail visibly.
- Empty/partial interview participation and email failure must remain honest, durable states.

### Task 1: Recover native addon and original baseline
Files: isolated Odoo addons/t10_jobhunter_chat, docker-compose.jobhunter.yml, .env.jobhunter.example.
Interfaces: preserve docs/chat-contract.md v1 unchanged.
- [ ] Recover exact source writes from prior session transcripts into isolated checkout; protect current shared branch.
- [ ] Verify recovered files against prior patches and syntax, run backend baseline and native original tests in disposable environment.
- [ ] Save sanitized evidence and recovery inventory, independent spec/quality review.

### Task 2: Odoo workflow ownership and typed candidate page
Files: addon models/workflow.py, controllers/workflow.py, templates/security/tests; docs/workflow-contract.md.
Interfaces: fixed operations and data shapes in workflow-contract.md, same signed bridge callback and authenticated BFF.
- [ ] Add failing behavior checks for private cases/criteria/versioning, scheduling validation/approval/idempotency, tokens/answers and source validity.
- [ ] Implement Odoo-owned workflows, controlled booking/mail, public typed session and extension callbacks; preserve original read behavior.
- [ ] Run native tests and independent review; expose only typed operations.

### Task 3: Actual specialist orchestration and conversation associations
Files: backend/app/workflows.py, workflow_schemas.py, service/store/provider/schemas, backend/tests/test_workflows.py.
Interfaces: workflow-contract.md; Submit gains optional workflow request with candidate/action/payload; BFF allocates/scope-validates it.
- [ ] RED tests for actual role routing, needs-input, candidate association/reuse and limits; implement validated specialist handoffs and exact citations.
- [ ] Preserve existing API clients/history via additive migrations; persist focus/associations and replayable workflow summaries.
- [ ] Run backend/Postgres/Slack regression suite; independent review.

### Task 4: Native workflow controls and disposable stack
Files: addon static/src/chat.*, candidate template, e2e Compose/scripts in backend.
- [ ] Add focus selection/criteria approval/scheduling preview/confirm/report controls and accessible workflow state; maintain existing composer/history.
- [ ] Start isolated Odoo/backend/PG/SMTP stack, seed synthetic recruiter/candidates/companies, deterministic provider only in test image.
- [ ] Explore actual desktop/candidate UI with Playwright before writing browser tests; Hoot regression checks.

### Task 5: Executable Playwright matrix and harness evidence
Files: backend/e2e, docs/verification-workflows-2026-10-31.md, harness scoped tasks/evidence.
- [ ] Write/run Playwright happy chain plus approved failure/isolation matrix with real BFF/routing and captured SMTP; explicit waits, traces/screenshots.
- [ ] Complete independent whole-change review and required checks; resolve findings and record actual counts/limitations.
- [ ] Record scoped harness status via revision-checked helper; live email pending configuration, historical/backlog unchanged.
