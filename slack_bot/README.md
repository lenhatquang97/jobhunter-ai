# Jobhunter Slack pilot

A separate local worker for HR/tech-lead interview coordination. It does not call
the Odoo bridge or change Odoo records. App/bot display name: **Jobhunter**.

## Install the Slack app

1. Open [Your Apps](https://api.slack.com/apps) in the intended workspace's browser
   session. Choose **Create New App → From an app manifest** and select that
   workspace. Paste [manifest.json](manifest.json) into the JSON editor, review,
   and create the app. If a Jobhunter app already exists, inspect it before
   creating another or changing its permissions.
2. Review **OAuth & Permissions** and **Install to Workspace**. The four bot scopes
   are `chat:write`, `im:write`, `im:history`, and `users:read`. No channel history,
   calendar, files, email lookup or Odoo permissions are requested. Workspace
   administrators may need to approve installation.
3. In **Basic Information → App-Level Tokens**, create an app token named
   `Jobhunter local pilot` with **connections:write** only. Socket Mode is already
   enabled by the manifest. The generated token starts `xapp-`.
4. Copy the installed **Bot User OAuth Token** (`xoxb-`) from OAuth & Permissions.
   Store both tokens locally in `.env.slack`; never paste tokens into a chat,
   ticket, screenshot, log, commit or command line.
5. Get the Slack workspace/team ID and the pilot HR/lead **member IDs** from
   Slack profiles (**More → Copy member ID**). Use IDs rather than email/name
   matching. The initial pilot needs two distinct active internal members.

Slack references: [Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/),
[DM events](https://docs.slack.dev/reference/events/message.im/),
[message posting](https://docs.slack.dev/reference/methods/chat.postMessage/).

## Local configuration and startup

From the repository root:

```sh
cp .env.slack.example .env.slack
chmod 600 .env.slack
```

Fill the workspace ID, JSON arrays of HR/lead IDs, Slack tokens, OpenAI API key,
and an independent URL-safe database password in that ignored file. Use an
OpenAI model available to your account with Responses structured-output support;
the initial model default matches the existing Jobhunter service. Do not use
Odoo's database, DB user, bridge secret or capability credentials.

```sh
docker compose --env-file .env.slack -f docker-compose.slack.yml build
docker compose --env-file .env.slack -f docker-compose.slack.yml up -d
docker compose --env-file .env.slack -f docker-compose.slack.yml logs --tail=30 jobhunter-slack
```

The database and worker expose no host ports. Slack and OpenAI connections are
outbound. An advisory lock prevents a second worker using the same database.
Startup checks the bot's workspace and configured active internal members.
Compose persists state in the `jobhunter-slack_slack-data` volume. Do not remove
that volume to restart or upgrade the worker.

**The computer and Docker must remain awake.** There is no always-on hosting in
this pilot. Restart retains pending work, but cannot deliver messages during
downtime or recover Slack events that Slack no longer redelivers.

For a native Python run, first provision a separate local PostgreSQL database/user
and set its URL in `.env.slack`:

```sh
uv sync --frozen --extra slack
PYTHONPATH=slack_bot uv run --extra slack python -m jobhunter_slack --check
PYTHONPATH=slack_bot uv run --extra slack python -m jobhunter_slack
```

`--check` verifies bot credentials, participant membership and database connectivity;
the app-level Socket Mode token is exercised when starting the worker. Do not run
the native worker alongside Compose. Diagnostic output contains codes/IDs only;
configuration and provider exceptions are not printed.

## Use Jobhunter

Open **Apps → Jobhunter → Messages**. Example HR message:

> Please confirm interview APP-TEST-001 with @your-tech-lead on 4 November 2026,
> 14:00–15:00 Vietnam time. Meeting link: https://example.com/test-meeting

Replace the example with a future date. Vietnamese works too:

> Nhờ xác nhận phỏng vấn APP-TEST-001 với @tech-lead ngày 4/11/2026,
> từ 14:00 đến 15:00 giờ Việt Nam.

Jobhunter asks for missing information. HR reviews the absolute date, start/end,
timezone and recipient, then clicks **Send request**. Typing “yes” does not send or
confirm a request. The assigned lead receives **Confirm** / **Request change**.
Each interview uses a separate thread in each person's DM with the bot. Ask for
**status** to get current cards; reply in a particular thread to revise it. For
references outside a thread use the full request ID displayed on its card.

- HR uses **Change time** to provide revisions, or **Cancel** to review cancellation.
  Sending a revision requires new lead confirmation. Old buttons and reminders
  become ineligible; a lead change withdraws the old lead's request.
- A change request suspends the old schedule. An unanswered request expires at
  its proposed start. Neither means the candidate agreed or a booking exists.
- Interview reminders go to both people at24h and30m before start. Already elapsed
  deadlines at confirmation are skipped. Following downtime, relevant missed
  upcoming reminders are consolidated rather than replayed in a burst.
- Confirmation follow-up is once after4 working hours. Working hours are
  Mon–Fri09:00–18:00 in Asia/Ho_Chi_Minh; no holiday calendar is included.
- Feedback is requested at the planned end, deferred to the next working opening
  if needed. One follow-up goes to the lead and HR next working day09:00 after
  the actual request delivery. **Feedback submitted** is the lead's self-report,
  not verification in another system. **Interview did not happen** stops feedback
  reminders and tells HR. No written feedback is collected by this app.
- Optional meeting/feedback-system links come from HR and are never fetched.
  Source permissions remain the responsibility of the linked system.

## Delivery issues and operations

Stop with `docker compose --env-file .env.slack -f docker-compose.slack.yml stop`;
restart with `up -d`. Changes to participants, roles or credentials require a
worker restart. Inactive/deactivated/guest users fail membership checks before
inbox processing and before delivery. Removed roles cannot read their old context
or receive its queued messages even if the person retains another bot role.

Known rate limits honor Slack's Retry-After and have a bounded retry count. A
crash during a post, timeout or other ambiguous post outcome is **uncertain** and
is not automatically resent. Ask Jobhunter for **status** to see current interview
delivery issues. Check code-only worker logs for draft/connection issues. Verify
the DM in Slack before explicitly sending a replacement request; do not manually
change database delivery statuses to force retries.

Expected safe error codes include `configuration_invalid`, `credentials_missing`,
`slack_auth_failed`, `workspace_mismatch`, `participant_unavailable`,
`worker_already_running`, `rate_limited`, and `delivery_uncertain`. Resolve the
specific condition and restart; never enable SDK/debug payload logging to diagnose
real candidate data. Tokens can be managed/revoked in Slack app settings by the owner.

For initial acceptance, use synthetic references and the selected pilot members:
review → request → confirm → request change → revised confirmation → cancel.
Check language and both private threads. Timing/restart tests below use a controlled
clock; they are not evidence of real24h message delivery. A real provider smoke is
separate from fake-provider tests, and live Slack delivery must be reported separately.

```sh
uv run --extra slack pytest -q
uv run --extra slack python backend/scripts/test_with_postgres.py -q
uv run --extra slack ruff check backend slack_bot
uv run --extra slack ruff format --check slack_bot
```

Tests use only synthetic data. The PostgreSQL helper creates and removes its own
temporary cluster. No mobile UI tests are authored by this feature.
