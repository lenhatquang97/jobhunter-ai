# Reviewed workflow and demo redeployment

User approved the plan and implementation on 2026-10-31. Target existing AWS
Jobhunter service, beginning at source `735d2e7` and live task `jobhunter:5`.

1. Restore production addon compatibility; keep native skills, hide automated
   comparisons, remove automatic booking privileges, and require current criteria
   plus browser confirmation of the exact scheduling draft and interview guide.
2. Keep `gpt-6.1-sol` / high and canonical HTTPS interview links. Add the create-only,
   database/company-guarded English demo importer: 5 jobs, 15 candidates, 20 documents,
   25 skills. Preserve unrelated records and user edits; rerun must create nothing.
3. Verify backend/native/browser behavior using disposable Odoo/PostgreSQL and
   the production image with a separately labeled provider fixture/local capture.
4. Review, commit/push `main`, publish an immutable ARM64 digest, inspect app/service-
   only Terraform plans and perform stop/upgrade/seed/start maintenance.
5. Verify the actual public site with real OpenAI, isolated recruiter/candidate
   contexts, responsive web views, correct share origin and access boundaries.

Web testing only per user clarification. SMTP and cron remain disabled; public
booking must fail safely until SMTP is configured. Successful booking/invitation
capture is verified in the disposable stack, never claimed as live delivery.
