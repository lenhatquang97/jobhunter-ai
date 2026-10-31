# Reviewed release verification — 2026-10-31

The user authorized an existing AWS redeployment, web verification, and English
synthetic data: five roles and fifteen candidates. No new interview feature was
added. Existing interview/share behavior and confirmation boundaries were checked.

## Local evidence

- Full backend/Slack suite against disposable PostgreSQL: 201 passed; one existing
  upstream Starlette/httpx deprecation warning.
- Native Odoo addon maintenance: 66 tests passed, zero failures/errors, including
  approval/source access, job creation and calendar/session concurrency checks.
- Production ARM64 image, real Odoo/PostgreSQL and separately injected synthetic
  provider: responsive web, CV documents, direct typed submission/report and
  reviewed booking/local invitation capture. These checks prove host behavior,
  not provider judgment or inbox delivery.
- Demo ORM import: five jobs, fifteen applicants, twenty readable JD/CV files,
  twenty-five skills, twenty-five job skill requirements, thirty-five candidate
  declarations. Dry run made no writes, repeat created zero records, and an edited
  demo note was preserved. Unexpected database and changed ownership are rejected.
- PDF/DOCX/TXT fixture content is parsed with the production document libraries.
- Eight Node chat/origin-gate tests pass. Changed-file Ruff checks, diff whitespace,
  Compose configuration and Terraform validation pass. The full repository Ruff
  scan has thirty inherited findings in unrelated standalone/legacy code (mostly
  import ordering); they were not silently treated as passing.

Regression checks first reproduced criteria auto-approval, dispatch bypass,
unchanged guide approval version, loss of nullable external calendar-user fields,
finish-before-acceptance, and reuse of an older pre-interview report. The release
requires explicit approved criteria and exact browser-confirmed draft/guide;
repeated confirmation creates one booking. Missing inputs stay unbooked.

The native skills UI displays declarations, not matching scores/gauges or ranked
matching-talent actions. The search input and adjacent dropdown have matching
visible outer borders; the internal input edge intentionally has width zero.

## Live boundary

Production uses `gpt-6.1-sol` with reasoning `high` and the canonical public HTTPS
Odoo origin. SMTP and cron stay disabled. Live booking must fail safely with no
calendar/session/mail effects until SMTP is deliberately configured; successful
booking/captured mail is separate disposable evidence. Physical mobile testing is
excluded by user instruction. Standalone React and legacy voice/RAG addons are not
hosted by this service.

## Known minor limitation

A booked-invitation decline whose acknowledgment is lost cannot currently be
replayed through the HTTP boundary: it shows unavailable rather than the recorded
decline. This does not duplicate booking/mail, and booked invitations are disabled
in the live SMTP-disabled deployment. No additional interview work was added.

AWS rollout and real-provider results are recorded below after deployment.

## AWS rollout and public-site evidence

Application commit `3c021f4` was pushed to main and deployed as `jobhunter:6`.
Immutable runtime digest:
`sha256:997626f1030a6ca8fac19d63893934d338d7272fd42fe362d074d1b2d527d924`.
The published artifact matches the browser-tested ARM64 image. The reviewed stop
plan changed only the application definition/service; the restart changed only
service desired count. The previous task was explicitly verified STOPPED before
upgrade. Addon upgrade, live dry run, and demo apply all exited zero. The final
full Terraform plan exited zero with no changes.

The service and ALB target are healthy. Inspection of the running API process
confirmed `gpt-6.1-sol`, reasoning `high`, all four trusted adapter factories, and
`https://jobhunter-workspace.netlink269.chatgpt.site` as public origin. Aurora has
seven-day PITR, encryption and deletion protection; the recorded pre-maintenance
restore point was 2026-10-31T10:31:43.916Z. No restore drill is claimed.

Eight live HTTP boundary checks passed: native entry/login, secure cookies, CSRF
rejection, blocked management routes, hidden internal API, WebSocket101/pong and
unauthenticated direct-origin denial. Separate cross-recruiter checks rejected
private history/list access and invalid candidate tokens, while target history
sources remained current.

The live import audit confirmed all approved counts and repeated the importer
with zero creations. SMTP and the expiry cron are disabled; reference RAG addons
are uninstalled. The live native CV uploader also accepted one separately tracked
`[DEMO TEST] web-upload.txt` on the new Indigo fixture, persisted it after reload,
and exposed it through the normal permission-checked document bridge. This test
attachment is additional to the twenty seeded JD/CV files.

Actual OpenAI findings matched the deliberately constructed evidence cases:
Alex five supported; Blair two supported and three unknown; Casey five unknown.
These are criterion evidence states about fictional self-reports, not candidate
rankings or suitability scores. The existing private typed link used the correct
public HTTPS origin, accepted five explicitly synthetic answers in a separate
browser context, and produced a refreshed report containing those current answer
sources even when an older pre-interview report existed.

Initial browser attempts were not treated as passing: opening an editable native
form caused timestamp changes after the criteria snapshot, and stale approval
was correctly rejected before a provider run; a retry also hit a browser network
interruption. The verified chat path selects the current record through Add Odoo
context → Applicants, avoiding unrelated form autosaves. Explicitly re-review
criteria whenever native record changes invalidate a previous snapshot.

Private evidence lives under `.state/evidence/release-live/`; credentials, bearer
links and raw provider payloads are excluded from the report and public artifacts.

Final public browser result: **3 passed** with the real configured OpenAI provider
in 240.69 seconds. The reviewed full workflow produced an unbooked preview; the
explicit confirmation returned needs-input while SMTP was disabled, with no
reservation/invitation shown. The disposable capture test separately proved one
confirmed reservation and queued invitation. All four web viewport checks passed.
The final backend/PostgreSQL suite again passed all 201 tests. Remaining warnings
are upstream dependency deprecations, not hidden failures.
