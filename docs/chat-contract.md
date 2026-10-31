# Jobhunter Chat v1 boundary contract

This is the shared implementation interface for the service and native Odoo addon. JSON uses snake_case. All timestamps UTC ISO8601. IDs for service objects are UUID strings; Odoo record IDs are positive integers.

## Principal and service authentication

`Principal = {database_uuid: str, user_id: int, company_ids: list[int]}`. Sort/deduplicate company IDs; empty company scope is invalid. Scope conversation ownership by all three values. Odoo derives the principal from its session, never browser-supplied UID/company values. Reject superuser mode and non-recruitment internal users before granting access.

For native Odoo20 BFF, derive selected companies from validated native `cids` cookie/context, checking every ID against current user membership. With no selection use the user's main company, never all allowed companies. A forged selection fails rather than widening scope. Grant security metadata has own-only read ACL; creation/revocation are private server-only narrowly privileged operations, never generic user CRUD. Cancelled grants cannot be reactivated through ORM RPC. Business source reads always run without sudo.

Every internal `/v1` request uses `Authorization: Bearer JOBHUNTER_BRIDGE_SECRET` plus `X-Jobhunter-Principal` containing compact base64url UTF8 JSON Principal. Backend trusts only authenticated internal Odoo requests; no browser CORS or public service port. Never log headers/secrets. The service only has the bridge secret, not Odoo's capability signing secret.

`ContextRef = {model: 'hr.job'|'hr.applicant'|'ir.attachment', id: int, label?: str}`. Labels from browser are display hints only, never evidence. Backend validates models/IDs. Odoo validates source permissions and related-record restrictions.

## Internal service HTTP API

- `GET /health` reports process/database/configuration health without calling provider.
- `GET /v1/conversations` -> `{conversations: [{id,title,archived,created_at,updated_at}]}` (non-archived newest first; bounded list).
- `POST /v1/conversations` body `{}` -> conversation object.
- `GET /v1/conversations/{id}` -> `{conversation, messages: [...], runs: [...]}`; use a fresh validation capability supplied below before returning/reusing source-derived history.
- `PATCH /v1/conversations/{id}` body `{archived: bool}` -> conversation.
- `POST /v1/conversations/{id}/messages` body `{client_message_id: UUID, text: str, context_refs: ContextRef[], capability: str, odoo_database: str, retry_of_run_id?: UUID}` -> `{run_id, message_id, status}`. Odoo preallocates `run_id` and includes it as body `run_id` so its signed capability/grant names the exact run. Validate unique run ID and message idempotency. Duplicate accepted submission returns the original run. Another active run produces HTTP409.
- `GET /v1/runs/{id}/events?after_seq=0` -> `{run: Run, events: RunEvent[], next_seq: int}`. Bounded page, increasing sequence. Idempotent replay.
- `POST /v1/runs/{id}/cancel` -> `{run_id,status}`. Cooperative cancellation, preserve partial draft; prevent new callbacks, ignore late results. Only acknowledge cancelled after transition.

GET conversation/events requests carry `X-Odoo-Capability` and `X-Odoo-Database` for fresh source validation. This is a separate short-lived validation grant, allowed for terminal runs; it cannot execute model tools. Requests without such a capability may return source-free content only; source-derived messages/events must be withheld rather than trusted.

These GETs also carry `X-Odoo-Validation-Run`, the UUID bound into the validation-only grant. The backend uses that UUID as callback params.run_id for validation, independently of the historical run whose source list is checked. Validate each historical source list separately and apply its states to its actual run. Do not decode opaque capabilities or use wildcard execution grants. For active execution reusing historical evidence, validate using the new active run's execution grant/run ID.

Submission idempotency compares text, canonical context model/ID pairs and retry reference; exclude proposed run_id, capability and display labels, which can differ when the BFF retries. A reused client ID with changed content produces409.

`Run = {id,conversation_id,status,created_at,started_at?,finished_at?,error?:{code,message},source_state:'current'|'outdated'|'unavailable', retry_of_run_id?}`. Status: queued|running|completed|needs_input|failed|cancelled|interrupted. `Message = {id,role:'user'|'assistant',text,run_id?,created_at,context_refs:[],sources:[],visibility:'visible'|'outdated'|'unavailable'}`. Mask assistant text and sensitive tool events when source access is unavailable. Outdated text may be displayed explicitly labeled, but never reused as current model context. User-authored input remains visible to its owner.

`RunEvent = {run_id,seq,type,timestamp,agent_id?:str,parent_id?:str,tool_call_id?:str,payload:object}`. Types: run.queued|run.started|run.completed|run.needs_input|run.failed|run.cancelled|run.interrupted; agent.started|agent.completed; tool.started|tool.completed|tool.failed; message.delta|message.completed; source.invalidated. agent_id is Orchestrator or Report. Public payloads are separately allowlisted; no raw provider payloads, reasoning, capabilities, binary files or secrets. Settle open activities from terminal state. Text deltas carry `{text:str}`; message.completed carries message_id/text/sources. Tool start `{name,arguments}`; completion `{name,summary,sources}`; failure `{name,code,message}`. Arguments/results bounded and permission-filtered.

## Backend callback into Odoo

Pinned `ODOO_BASE_URL`, never a URL from model/user. POST `/jobhunter/chat/tool` as JSON-RPC: `{jsonrpc:'2.0',method:'call',id:<opaque>,params:{run_id,operation,arguments}}`, `Authorization: Bearer <Odoo signed capability>`, `X-Odoo-Database: <db_name>`. Decode standard `{result:...}` / `{error:...}`. Fixed timeout and no retries. Never expose errors containing raw HTTP/provider payloads.

Odoo uses scoped `odoo.tools.misc.hash_sign` / verify helper, expiry <=5min. Capability claims include audience/version/database UUID/db/user/company IDs/run ID/purpose/tool allowlist. Active per-run grant checked on every tool callback; cancel revokes it first. Reconstruct environment with original user, clean allowed_company_ids and `su=False`; UID1 remains prohibited. Recheck active user, group and current company memberships each call. Validation grants allow only `validate_sources` and can be newly issued for history reads after run end.

Active execution grants include server-only `validate_sources` as well as the six business tools, so the backend can recheck evidence before Report delivery. This validation operation is never registered as a model-callable tool.

`operation` is one of model tools below or server-only `validate_sources`. Callback result `ToolResult = {ok: bool, data: object, sources: SourceRef[], error?:{code,message}}`. Failures are explicit, never substitute fake data. On permission failures use `access_denied` without confirming record existence.

`SourceRef = {key: str, model: str, record_id: int, field?:str, attachment_id?:int, title:str, write_date:str, checksum?:str, locator:str, excerpt:str}`. Registry key is server generated and immutable for that version/location. Store actual bounded excerpt. Citations use `[S1]`-style keys generated from source registry (key S1 etc per run); model cannot invent URLs. Final answer citation keys must exist and quotes must match referenced excerpts. Odoo navigation is generated from model/record ID; attachment link uses normal permission-checked Odoo content route.

`validate_sources` arguments `{sources: SourceRef[]}` -> `data:{states:[{key,state:'current'|'outdated'|'unavailable'}]}`. Check parent/attachment access, write_date and checksum; no extraction required. Business reads never use sudo.

Validate each historical run's source list independently: S1 keys are run-local. Normalize Odoo write_date to UTC ISO8601 before comparison. Independently restrict source models/fields, attachment actual parent (even public attachments), and reject URL/protected-field attachments.

## Strict model tools

All function schemas strict, additionalProperties false, all fields required (nullable where stated). Server enforces bounds independent of provider schema:

- search_jobs `{query:str,limit:int}` (1..20).
- get_job `{job_id:int}`.
- search_applicants `{query:str,job_id:int|null,limit:int}` (1..20).
- get_applicant `{applicant_id:int}`.
- list_documents `{model:'hr.job'|'hr.applicant',record_id:int}`.
- read_document `{attachment_id:int}`.

Every run callback also receives `context_refs` in arguments wrapper (outside model arguments); Odoo verifies they match grant-issued context. With selections, limit targeted reads/searches to selected records/related job/applicant/documents. Without selections allow explicit ACL-scoped search. No arbitrary ORM domains/methods/fields, URLs or SQL. Ambiguous candidates need selection, not a guess. Allowed job fields: id/name/active/description/requirements/department_id/company_id/write_date. Applicant: id/partner_name/job_id/stage_id/active/company_id/create_date/write_date. No scores/legacy reports/contact details/unrestricted notes.

For the six model tools the exact callback params.arguments wrapper is `{arguments: <strict model arguments>, context_refs: ContextRef[]}`. For server-only validate_sources it is directly `{sources: SourceRef[]}` without that wrapper.

Successful data shapes are `{job: {...}}`, `{jobs: [...]}`, `{applicant: {...}}`, `{applicants: [...]}`, `{documents: [...]}` and `{document: {...}}`, matching the tool. Selected-context anchors are a union: selected applicant permits itself, its job and its documents, not sibling applicants; selected job permits itself and ACL-visible applicants for that job and their related documents; selected attachment permits itself and parent record metadata (and parent's job for applicant), not sibling documents/applicants. Revalidate anchors on every call. Canonicalize context refs by sorted unique model/ID pairs, labels never grant authority.

Attachment metadata through parent ACL: id/name/mimetype/file_size/checksum/write_date/res_model/res_id. `read_document` returns `data:{document:{id,name,mimetype,content_base64,checksum,write_date,res_model,res_id}}`; raw contents never enter public events. Read only parent-related files; max10MiB; backend extracts bounded text with exact PDF page/DOCX paragraph/TXT line refs (100000 characters max, truncated explicit). Unsupported/scanned/encrypted content returns needs-input error. Do not follow embedded links. The backend registers extracted fragments as SourceRefs derived from verified document metadata.

## Native BFF/UI

Auth-user JSON-RPC POST `/jobhunter/chat/rpc` with `{operation,arguments,csrf_token}`. Explicit request.validate_csrf for all requests; no reliance on JSON-RPC csrf flag. Operations: health; conversations.list/create/get/archive; messages.send; runs.events/cancel; context.search/documents. BFF owns capability issuance, ID allocation, grant revocation, principal derivation and proxy timeouts. GET retrieval/cursor calls still use this POST wrapper. BFF returns service response body directly as JSON result. Backend errors become `{error:{code,message}}` for actionable UI; business/tool errors remain typed results. Health returns backend /health without credentials/raw errors.

Context picker uses authenticated native read tools without backend/model. `context.search` arguments `{model:'hr.job'|'hr.applicant',query:str}` returns `{records:[ContextRef]}`. `context.documents` arguments `{model,record_id}` returns `{records:[ContextRef]}`. Do not send selected record bodies from browser as evidence.

UI polls events every500ms only while active; after_seq prevents duplicates. Record form links via action service; attachment links normal Odoo ACL. No Work tab/mic/voice controls. Markdown output sanitized, code/plain strings escaped, links only approved source URLs or safe http(s) links without executing HTML. No hidden reasoning. Private history only in current principal/company context.

## Execution and storage

Use own Postgres DB; SQLite allowed only in unit tests. Single API worker. Persist input before scheduling, enforce active-run/idempotency in storage. Startup marks queued/running interrupted, keeps partial results. No automatic resume/retry. Limits120s, max6 provider requests,8 tool calls; OpenAI SDK max_retries=0. Orchestrator reads tools, Report tool-free only when real retrieved evidence needs summary; both fixed shared+role prompts v1.0. Request store=False. Never expose encrypted/provider reasoning in events. Before Report delivery and every historical reuse revalidate sources. General chat with no evidence may complete directly. Missing context/capability -> needs_input, attempted error -> failed; cancelled/interrupted retain real state. No scores, rankings, stage mutation, outreach or booking.

Backend environment names: JOBHUNTER_DATABASE_URL, JOBHUNTER_BRIDGE_SECRET, JOBHUNTER_ODOO_BASE_URL, JOBHUNTER_OPENAI_API_KEY, JOBHUNTER_MODEL, JOBHUNTER_REASONING_EFFORT. Uvicorn app.main:create_app --factory, PYTHONPATH=backend, one worker. Odoo uses JOBHUNTER_API_URL and the same JOBHUNTER_BRIDGE_SECRET; callback origin is http://odoo:8069 inside Compose.

## User-approved job creation confirmation extension — 2026-10-31

`create_job` is proposal-only, despite its retained tool name. The signed execution
capability carries optional `job_scope:{conversation_id:UUID}`; only that scope
permits proposal preparation. The tool accepts exact title, optional existing
department name, hiring count, plain description and requirements, and returns
only `job_proposal:{id,status:"proposal_ready"}`. The model receives no confirmation state or created-record data. It never creates a position, returns a created
job body, or bypasses business-record source validation. Tool events omit draft
arguments. Retrieved source instructions cannot authorize a business write.

Odoo stores immutable private proposals keyed by recruiter, exact selected-company
scope, conversation and normalized payload hash. Repeating the same fields in a
conversation, including a retried model run, reuses the same proposal/receipt.
A different intended identical opening requires a new conversation. Department
ambiguity/missing records requires input; departments are never created.

BFF `jobs.proposals` accepts `{conversation_id}` and returns ACL-checked proposal
cards. `jobs.confirm` accepts `{proposal_id,review_hash}` through authenticated,
CSRF-validated browser requests only. No model grant permits confirmation.
The card shows company, title, department, hiring count, description and
requirements and requires an explicit Confirm create position click. A row lock
and savepoint bind native-user ORM creation and the receipt atomically. Duplicate
confirmation, lost acknowledgments and retries return the recorded job instead
of creating another. Current owner/company, department and business ACLs are
rechecked at confirmation and retrieval, including native ORM reads/exports.
No sudo is used for job creation, and no production roles are expanded.

Created positions are shown through native Odoo record links fetched from current
ACL-checked receipts, not injected into assistant history as uncited evidence.
Approval fields are historical snapshots, not a claim about the current job body.
No rankings, suitability scores, candidate stage changes, recruiting automation
or external outreach are added by this extension.
