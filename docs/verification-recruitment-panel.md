# Recruitment panel implementation and verification

Implemented 2026-10-31 in jobhunter-ai. Local app: http://localhost:28069/odoo/hr.applicant (or Recruitment → Jobhunter Chat). The provided admin login was verified in desktop Chrome. The running stack has been restored to the real provider, with database health OK and Filter, Interview and Report configured. Schedule remains unavailable in this integration.

## Delivered behavior

Native Recruitment list, kanban and applicant forms remain on the left. Our own Jobhunter AI panel sits on the right; it does not use OdooBot. It is 420 px by default, adjustable 360–640 px, collapsible, and an overlay below 900 px. Explicit Use in chat selects the current record; navigation never silently changes the selected applicant. Drafts and conversations survive collapse/native navigation.

The orchestrator supports typed natural-language Filter routing and explicit workflow actions. Adapted Filter produces cited criteria findings (supported/partial/unknown/conflicting). Interview prepares five English/Vietnamese typed questions without automatic follow-ups. The recruiter explicitly creates a private expiring link; answers are submitted explicitly and immutably. Interview/Report summarize actual evidence without scores, rankings, hiring recommendations, booking or email in this flow.

The native addon and both original RAG addons were copied into this repository. odoo/imports.json records original file hashes and source revisions. Both original RAG import checksums were verified unchanged; their addons are not installed or mounted. Active adapted code is backend/app/agents and the repository-owned native chat addon.

## Results

| Check | Result |
|---|---|
| Backend + Slack suite, SQLite unit mode | 138 passed, 1 PostgreSQL-specific skip |
| Full suite using disposable native PostgreSQL | 139 passed |
| Native Odoo/PostgreSQL addon suite | 51 passed, 0 failures/errors |
| OWL/Hoot | 17 passed, 49 assertions |
| Browser happy path + navigation/responsive test, controlled provider | 2 passed, 72.54 s |
| Same browser tests, real OpenAI provider | 2 passed, 99.01 s |
| Ruff checks and formatting |Passed |

The real-provider happy path starts with a typed request to the Orchestrator, invokes Filter and Report, prepares five Interview questions, explicitly creates a link, recovers the same link after a full reload, submits five answers in a separate candidate browser context, then invokes Interview summary and Report. The second browser test verifies draft retention, explicit candidate selection, focus preservation across native record navigation, collapse/reopen, width bounds and the narrow drawer.

Controlled tests use real migrated adapters and a deterministic provider fixture; they do not prove AI semantics. The separately recorded live run uses actual Responses calls with synthetic records. Its successful result does not establish future model reliability or independent verification of candidate self-report. Provider/model/bridge secrets were not exposed in browser requests or screenshots. No invitation or calendar action was part of this flow.

## Screenshots and traces

- [Actual computer/Chrome overview](evidence/recruitment-panel/desktop-overview.png): native Odoo kanban plus our separate AI panel, signed in as admin.
- [Live Filter evidence/activity](evidence/recruitment-panel/live/filter-from-trace.jpeg).
- [Live five-question guide](evidence/recruitment-panel/live/guide-from-trace.jpeg).
- [Candidate typed page, deterministic-provider walkthrough](evidence/recruitment-panel/candidate-typed-deterministic.png).
- [Live final Interview/Report summary](evidence/recruitment-panel/live/final-summary.png).
- [Narrow responsive drawer](evidence/recruitment-panel/live/narrow-panel.png).
- [Machine-readable verification](evidence/recruitment-panel/verification.json).

The two live trace-frame images are unmodified frames from the successful real-provider Playwright recording, immediately before the corresponding screenshot actions. This avoids confusing them with deterministic captures. Private live Playwright trace ZIPs are preserved under .state/evidence/panel-live-traces; they contain synthetic session URLs, so are kept outside tracked/public evidence. Future screenshots default outside Playwright's cleanup directory under .state/evidence/panel.

## Corrections verified during integration

- Empty-chat layout no longer covers workflow approval controls; candidate inputs stay hidden until session loading completes, avoiding lost early typing.
- A nonsecret, scoped request UUID survives reload/lost acknowledgment. Authenticated recovery rederives the same link without extending expiry. The UI states expiry is seven days after creation.
- Expired/unavailable historical session answers do not block fresh workflows; current-guide session metadata is independent of a newer booking.
- Private streamed deltas keep cancellation/deadline checks without a per-token Odoo freshness call. Source validation still occurs before/after provider work and before delivery; a revocation-during-stream regression prevents publication.
- Source validation memoization is limited to one synchronous tree, including identity, anchor/user/company scope, write invalidation and cycle/depth guards. A six-artifact DAG previously required 63 resolutions; the verified bound is 6.
- Invalid live quotation formatting was rejected. Summary instructions now prefer cited paraphrases and require an immediately attached citation for exact quotations; the checker was not weakened.

Independent review found no remaining Critical or Important issues after the fix review. The minor expiry label was corrected. Normal unit fixtures explicitly ignore private runtime .env configuration, keeping deterministic tests isolated from real credentials/adapters.

## Compatibility and local decisions

All work stays uncommitted in the requested unborn feature checkout; creating another Git worktree would require an initial commit, which was not authorized. Unrelated Slack files, sibling checkouts and existing application databases were preserved. The new Compose project owns separate volumes. No commit, push or cloud deployment was performed.

Odoo Integer fields cannot persist null through their normal ORM conversion, so direct sessions reserve negative draft_version values while historical booked versions remain positive. Additive guide_version/client_request_id fields preserve booking uniqueness and idempotency; native concurrency tests cover both paths. Cost of this convention is an explicit internal sentinel rule, documented in workflow-contract.md.

The imported embedding HTTP client is replaced by bounded lexical source-diverse retrieval to keep all provider calls inside the shared Responses runtime. Scanned/unreadable documents still require user input, as in the existing extraction contract. Scheduling, external email and voice remain outside this direct typed flow.
