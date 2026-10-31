# Odoo Recruitment Agent Panel Implementation Plan

User-approved in conversation 2026-10-31. Consolidate the native chat addon and both imported agents; keep Odoo Recruitment on the left and collapsible/resizable chat on the right. Typed direct interviews, no booking/email/voice in this flow. No candidate ranking, suitability scores or recommendations. Synthetic deterministic and separate live AI verification. No commits/push/deploy or existing recruiting record mutation.

### Task 1: Consolidate
Copy chat addon from isolated candidate-workflows checkout and two RAG addons from sea-hkt-t10 main f444ec8; record checksums. Originals are migration references only. Use existing Odoo image and independent repository-owned Compose stack.

### Task 2: Agent adapters
Implement async invoke(assignment, runtime) for Filter, Interview and Report via shared Responses gateway; reuse imported requirements/evaluation, retrieval/topic and report concepts. Exact citations, supported/partial/unknown/conflicting findings, five EN/VI typed questions, actual submitted answers only, no scores. Preserve existing limits and cancellation.

### Task 3: Direct interview ownership
Add prepare_interview routing and scoped workflow_prepare_direct callback. Version direct guides independently of scheduling. Browser-only workflow.interview.create(case_id, guide_version, client_request_id) explicitly creates hash-token session with seven-day expiry and idempotency. Do not send bearer links through model/event boundaries. Add fields compatibly with booked sessions.

### Task 4: Right panel
Reuse OWL chat across Recruitment list/kanban/form, default420px/resizable360..640px, collapse and keyboard, narrow overlay. Explicitly select current applicant chip; navigation never silently switches conversation focus. Preserve history/citations/activity/cancel/retry. Reuse for existing menu.

### Task 5: Verification
Explore actual desktop before writing browser tests. Run native/backend/Postgres/UI checks and deterministic full chain with real adapters. Separate live real-provider synthetic chain. Capture overall desktop, split UI, Filter findings, typed candidate page and final summary; redact credentials/tokens. Preserve traces and report exact proof boundaries. UI access requires unlocked Mac/browser enabled; credentials required for live checks.
