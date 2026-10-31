# Jobhunter Slack pilot design

Approved in chat on 2026-10-31; the user requested implementation.

## Intent and chosen approach

Jobhunter coordinates HR-proposed interview times with one assigned tech lead through
separate private Slack DMs. It understands English and Vietnamese, asks for missing
details, and requires HR to review a structured card before sending a request.
The lead confirms or requests a change using buttons. HR controls revisions and
cancellation. Confirmation represents the lead's agreement, not the candidate's.

A conversational Python app was chosen over forms-only interaction. Bolt Socket
Mode avoids a public endpoint and fits a local pilot; forms-only would remove the
AI dependency but would not provide the chosen conversational experience.

## Boundaries

The worker is separate from the read-only Odoo chat service and has its own
PostgreSQL database/user. It never calls the Odoo bridge, changes business records,
books meetings, contacts candidates, ranks candidates, or collects feedback text.
Optional HR-supplied links are displayed without fetching their contents.
Workspace and HR/lead Slack IDs are explicitly configured, never inferred by AI.
Only the owner can create/edit/cancel; only the assigned lead can confirm, request
a change, report feedback submitted, or report that an interview did not happen.

## Timing and state

Use Asia/Ho_Chi_Minh and Mon–Fri 09:00–18:00 for working-hour follow-ups, without
a holiday calendar. Follow up once after four working hours and notify HR. An
unanswered proposal expires at its start. Confirmed interviews get reminders to
both people 24 hours and 30 minutes before start, skipping elapsed deadlines.
Ask the lead for feedback submission at planned end (next working opening if
outside hours), then follow up and notify HR next working day at 09:00. Submission
is self-reported; scheduled end is not proof that an interview took place.

Persist drafts, interview versions, per-person DM threads, inbox deduplication,
scheduled delivery and outcomes. Changed/cancelled/expired versions suppress old
buttons and jobs. Recover interrupted inbox work, consolidate missed reminders,
and mark interrupted/ambiguous sends uncertain instead of blindly retrying.

## AI, setup and validation

Use purpose-specific OpenAI Responses structured outputs, store=False, bounded
inputs and no action tools. Human buttons and application validation control all
state changes. Reply in each person's language, English before it is known.
Provide a Slack manifest, isolated local Compose setup, secret-free env example
and runbook. One worker runs while the computer is awake; no cloud deployment.
Use synthetic data, fake providers/Slack and controlled clocks for automated tests;
separately report any real Slack/provider smoke evidence. No commits or pushes.
