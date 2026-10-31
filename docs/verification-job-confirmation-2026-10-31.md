# PR5 guarded deployment verification — 2026-10-31

User approved adding explicit job-creation confirmation and duplicate protection
before deploying the newer PR. The model tool now prepares immutable private
Odoo proposals only; the signed conversation scope cannot confirm. The browser
shows exact approved fields and creates a position through native-user ORM only
after a CSRF-validated confirmation. Unique payload receipts, row locking and a
savepoint protect retries, concurrent confirmation and partial failures. The
model receives only a neutral proposal ID/status, never creation state or the
created job body. Generic ORM reads/exports also recheck private scope and current
business access.

The PR's unrelated merge regressions were fixed: missing legacy interview compute
and voice/score views removed; CV uploads restricted to saved applicants and
PDF/DOCX/TXT; search-menu access retained with the tested border fix. Added the
explicit upgrade-ui maintenance command for only t10_jobhunter_chat and
aho_recruitment_ui. No production roles, permissions, SMTP or cron were enabled.

## Local evidence

- Python/backend/Slack: 195 passed, 1 PostgreSQL integration skip, with synthetic
  test reasoning explicitly set to low (the deployed setting remains high).
- Native Odoo/PostgreSQL addon upgrade suite: 61 passed, 0 failures/errors.
  Initial test setup without capture configuration failed closed on SMTP/HTTPS;
  the official synthetic capture mode was then used. Confirmation success
  fixtures include actual native HR permissions; a separate denial check proves
  insufficient business permissions are not elevated.
- Real Odoo browser + PostgreSQL + real signed bridge and deterministic synthetic
  provider: draft preparation created zero jobs; the explicit button created one
  job; reload/history reopening recovered the approved receipt. Exact company,
  title, department, count, description and requirements were reviewed.
- Two simultaneous authenticated HTTP confirmations of a separate synthetic
  pending proposal returned the same job ID and increased job count exactly one.
  A later confirmation reused that receipt. Missing CSRF and altered review hash
  were rejected. No live provider claims are inferred from this fixture.
- Synthetic CV uploaded/saved through the real browser was discovered and read
  by the allowlisted bridge under normal user access, with the correct applicant
  parent. A new unsaved applicant showed a save-first message and no upload button.
- Independent read-only review found no blocking confirmation, idempotency or ACL
  issue. Its transient proposal-refresh issue was fixed by setting the completion
  marker only after a successful refresh.
- Scoped Ruff, JavaScript parsing and Git whitespace checks passed. Full-repo
  unrelated standalone lint findings remain recorded in the prior deployment log.

No live recruiting records were created or changed by these checks. Provider,
SMTP and booking behavior outside this scope was not reverified. Private fixture
logs/evidence are ignored under .state. Production rollout evidence is kept there
separately; this document describes checks completed before publication.
