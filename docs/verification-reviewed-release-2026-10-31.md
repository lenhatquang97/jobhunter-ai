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
