# Job creation confirmation implementation plan

User approved confirmation and duplicate protection before deploying PR #5.

**Goal:** Chat proposes exact job fields; only an explicit recruiter confirmation creates an Odoo position.
**Architecture:** Immutable Odoo proposals are private to recruiter and exact company context. Signed execution grants permit proposal preparation for one conversation. No model grant permits confirmation. Authenticated CSRF-checked browser confirmation creates a job and records its receipt atomically under a row lock.
**Stack:** Existing Odoo20/OWL3 and Python Responses host; no new service.

- [x] Add immutable proposal model, ACLs, exact payload hash, conversation/payload uniqueness, current department/company/job access checks and idempotent confirmation transaction.
- [x] Replace model-side creation with proposal-only callback. Return only proposal ID/status to Python; never present a draft as a created business record or exempt created records from source checks.
- [x] Add private list/confirm BFF operations and signed conversation scope; block direct model execution of job creation.
- [x] Render exact company, title, department, count, description and requirements with a Confirm create position button. Persist/recover receipts through Odoo and handle lost acknowledgments by reusing the same proposal.
- [x] Validate first preparation creates zero jobs; exact repeats/retries reuse a proposal; confirmation/double confirmation creates one job; altered payload/hash, cross-user/company, ORM edits and model confirmation fail. Confirm current ACL checks hide inaccessible results.
- [ ] Preserve validated CV/UI fixes. Run backend/native/browser checks, independent review, update contracts, commit/push authorized changes and deploy immutable image with scoped addon upgrade. Verify health, assets, history and reasoning effort high.

Exact same normalized fields in one conversation reuse the same proposal, including after confirmation. A different intended identical opening requires a new conversation. Different fields require a separate explicit confirmation. Proposals are not business evidence; generated chat never receives the created job body. The created record link is shown only through fresh Odoo ACL-checked proposal retrieval.
