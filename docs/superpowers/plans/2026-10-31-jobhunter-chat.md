# Jobhunter Chat Implementation Plan

Approved by the user on 2026-10-31. The native Odoo workspace follows the supplied ChatGPT references: white canvas, centered rounded composer, black user messages, readable assistant responses and expandable actual agent/tool activity.

## Constraints

Service: jobhunter-ai. Addon/local integration: sea-hkt-t10. Development guidance/progress: jobhunter-harness. Target Odoo 20.0-20261015. No automatic commits or cloud deployment. Work on codex/jobhunter-chat in the explicit checkouts to preserve sibling Compose paths; the backend has an unborn Git history.

V1: real OpenAI typed chat (English/Vietnamese), read-only existing Odoo records/documents, Orchestrator and actual Report synthesis, private persistent history and activity. Excludes voice, uploads, Work dashboard, side panels and full specialist workflows. Existing automation remains unchanged.

## Task 1: Backend service

- Build Python3.12 FastAPI/Pydantic/SQLAlchemy/Postgres/OpenAI service under backend/app with tests under backend/tests, root pyproject, lockfile, Dockerfile and env example.
- Follow docs/chat-contract.md. Persist conversations/messages/runs/events/sources, strict owner/company scope, idempotency, one active run/conversation, bounded async execution, replay, cancellation and startup interruption.
- Implement strict read-tool definitions, Odoo bridge callback, source-aware history/reuse, bounded document parsing, exact citations and Orchestrator->Report handoff. Use real Responses streaming; no automatic retries or fabricated activity.
- Add meaningful failing tests before behavior; verify the whole backend suite with fakes plus PostgreSQL integration where available.

## Task 2: Native Odoo addon and UI

- New t10_jobhunter_chat depends web/hr_recruitment. Use actual OWL20 client action and unified security/ir.access.csv.
- Implement authenticated CSRF-validated BFF, original-user/company read tools, signed revocable per-run grants, source validation and access-checked existing-document access. No business-data sudo or writes; reject superuser-mode grants.
- Full-page Recruitment submenu, private history/sidebar/new/archive, welcome/composer states, source picker/chips, 500ms active-run polling, stop/retry, sanitized Markdown/citations/copy and expandable actual activity. Responsive/accessibility/keyboard behavior.
- Add Compose overlay for API+separate Postgres, addon mount and environment; service internal to network. Unit/controller/Hoot tests and disposable DB browser walkthroughs.

## Task 3: Harness alignment and evidence

- Update backend binding to ../jobhunter-ai and required_paths to backend/app, backend/tests, AGENTS.md. Update observed context and decisions; preserve old observations.
- Add scoped chat tasks without completing broader APP/ODOO/SPEECH/AWS backlog. Add stable unborn Git content fingerprints with tests, retaining clean/dirty HEAD behavior and non-repo rejection.
- Run doctor/validate/harness suite. Record real sanitized evidence through revision-checked progress updates.

## Task 4: Integrated validation and review

- Install/upgrade/remove addon in disposable synthetic DB; verify permissions/company/attachment/capability/CSRF denials, no recruiting mutation, source invalidation/history hiding, source injection and unreadable documents.
- Verify incremental events, source links, exact citations, actual roles/tools, replay/idempotency/cancel/timeouts/provider errors/restart and UI screenshot against references.
- Mocked verification and live OpenAI smoke remain separate. Obtain fresh independent whole-change security/correctness review, fix actionable findings and rerun relevant checks.
- Deliver concrete entry point, changes, actual checks and remaining limitations; no deploy/commit/push.

## Review focus

Source revocation must hide derived history and exclude it from future prompts. Callbacks must not bypass Odoo permissions. Cancellation must revoke grants and not turn late output into success. Reconnect must not duplicate model calls. Public activity must show only real steps, safe payloads and verified sources.
