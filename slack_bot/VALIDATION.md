# Slack pilot validation — 2026-10-31

## Automated checks

- Fresh isolated default environment: `uv run --isolated --frozen pytest -q` →
  **109 passed, 1 skipped** (the optional PostgreSQL integration).
- Disposable PostgreSQL: `uv run python backend/scripts/test_with_postgres.py -q`
  → **110 passed**, including the Slack suite and the existing backend suite.
- `uv run ruff check backend slack_bot` and Slack formatting checks passed.
- The Python3.12 Docker image built and its CLI help ran successfully.
- Compose configuration passed; the isolated local database is healthy and schema
  creation succeeded from the actual Docker image. No host ports are published.

Provider/Slack unit tests use fakes; reminder timing/recovery tests use a controlled
clock. These tests are distinct from the live evidence below.

## Live evidence

- Actual OpenAI Responses structured extraction passed for synthetic English and
  Vietnamese requests, including the exact candidate reference, lead ID and times.
- The Jobhunter app was created and installed in the user-selected workspace via
  the existing Chrome session. The user approved scopes/token creation, final
  consent terms and the new member invitation before those steps proceeded.
- Bot authentication, active member lookup and a live Socket Mode WebSocket
  `hello` handshake passed. Socket Mode and interactivity were verified enabled.
- The user completed the invitation and joined the second account. Distinct HR
  and tech-lead member IDs were then configured, and production preflight passed.
- The local worker started successfully. A real HR setup DM was delivered and
  read back through Slack; it was subsequently updated to the ready instructions.
- A synthetic `APP-SLACK-SMOKE` proposal was entered in Vietnamese from the HR
  Slack desktop account. Its real review card showed the correct absolute date,
  time, timezone and assigned lead.
- HR clicked **Send request**. The second account received an English private
  request in Chrome and clicked **Confirm**. HR received the Vietnamese confirmed
  notice. Database state was `confirmed`, version1;24h/30m reminders for both
  people and the feedback request were persisted as pending jobs.
- HR reviewed and confirmed cancellation. Both accounts visibly received the
  cancellation. Database state became `cancelled`, version2; **zero pending or
  sending interview jobs remained**. The synthetic record remains for audit.

This validates real two-account submission, approval, delivery, confirmation and
cancellation. Actual24h/30m reminder delivery and a next-day feedback follow-up
have not been observed over elapsed wall time; those timings are covered by the
automated clock tests. No real candidate records or calendar events were changed.

## Review and implementation decisions

An independent read-only review found three important issues; each was reproduced
with a failing check and fixed: overdue follow-ups outside working hours, default
test collection without the optional SDK extra, and extreme timestamps poisoning
the inbox. The final recovery tests also caught premature cancellation of a future
feedback job, which was fixed before the live test.

- Work stayed in the existing checkout because it has no initial Git commit and
  contains other uncommitted work; no commit, push, branch migration or cloud
  deployment was performed. Existing backend source was not edited by this task.
- The approved plan was executed in this chat with one final independent review.
- Only the assigned lead self-reports feedback submission. Unanswered proposals
  expire at start; HR must propose a future time if confirmation arrives late.
- HR and lead must be distinct Slack accounts. One person can hold multiple roles,
  but cannot send themself an interview confirmation request.
- Current configured role checks apply to reads, buttons and delivery. Config
  changes require a restart; live active-member checks also run before processing
  and delivery.
- Withdrawal routing was treated as important for identifying the correct
  interview, so reassignment now retains the prior thread/reference/version and
  respects revoked lead roles.
- Single-worker throughput and keeping the local computer awake are explicit
  pilot limits. Public holidays are not part of the Mon–Fri working-hours calendar.

Credentials and personalized role configuration live only in ignored `.env.slack`
with mode0600. No credentials, personal email addresses or member IDs are included
in this validation document. See [README.md](README.md) for operating instructions.
