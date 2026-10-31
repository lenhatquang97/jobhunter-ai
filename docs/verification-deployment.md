# Published Jobhunter verification — 2026-10-31

Public application: https://jobhunter-workspace.netlink269.chatgpt.site

The Sites Worker proxies the native Odoo application, including candidate pages, to the protected AWS CloudFront VPC origin. Odoo uses the repository's custom Jobhunter AI panel. OdooBot is not used for AI routing; normal Odoo system-authored creation notices remain native Odoo behavior.

## Verified deployed behavior

- Real OpenAI, actual Filter/Interview/Report adapters: cited CV findings, five generated typed questions, five explicitly submitted synthetic answers, completed interview and cited evidence summary. Separate recruiter/candidate browser contexts. Reopening history recovered the same private link. The successful artifact identifies `live-openai-deployed`; there was no fake provider in the deployed image.
- Native login, Secure/HttpOnly cookie passthrough, missing login CSRF rejected, missing chat CSRF rejected, management/development routes denied, Python internal API absent from the public route space, direct CloudFront request denied403.
- Actual WebSocket upgrade101 and end-to-end ping/pong through Sites → CloudFront → ALB → nginx → Odoo, without an origin-domain handshake cookie.
- Forced ECS replacement changed the running task identity. The original browser login survived; Odoo records, three completed assistant messages and current source validation remained available. A second recruiter could neither list nor fetch the first recruiter's conversation. Invalid candidate tokens returned404.
- Aurora TLS certificate/hostname verification, separate database owners/extensions, seven-day PITR retention and 0.5–1 ACU configuration. An encrypted EFS recovery point actually completed with seven-day retention. No restore drill is claimed.
- Final IAM is scoped to project resources with an immutable runtime permissions boundary. Bootstrap credentials were disabled. A refreshed Terraform plan reported no changes. A separate RED/GREEN maintenance plan proves an app image update changes only the app task/service, preserving the retired initializer without expanding its permissions.

The first video attempt stopped at a five-second link-recovery assertion. The test now waits explicitly up to twenty seconds. The successful full run recorded no RPC error codes; this does not claim a diagnosed application failure from the initial timeout.

## Executable checks and private artifacts

Run from the repository root after obtaining the private deployment handoff:

```sh
uv run python e2e/check_deployment.py https://d3n8fh7gqtrxy6.cloudfront.net
uv run python e2e/check_isolation.py
uv run python e2e/record_demo.py
uv run python e2e/assemble_demo.py
```

`check_isolation.py` expects the seeded synthetic users and a completed demo conversation. `record_demo.py` creates fresh synthetic workflow artifacts and makes real provider calls. Local environment files, credentials, candidate URLs, failed recordings and network traces are excluded from Git. Viewport video excludes browser URL bars. The successful recording and screenshots are retained privately under `.state/evidence/deployed-attempt2`; the exported MP4 is approximately68seconds before its closing evidence view.

Supporting checks: Python141 passed/1 PostgreSQL integration skip in the ordinary run; TLS3; origin gate3; Sites gateway9; production ARM64 container browser2 with actual provider configuration. Prior unchanged addon verification is recorded separately in `verification-recruitment-panel.md`. Live deployed checks above establish the actual hosting path rather than inferring it from those local checks.

Single ECS task/writer provides continuous operation with brief deployment/restart interruptions, as approved. SMTP and cron sending are disabled. No real recruiting data was migrated, no candidate was contacted, and no ranking, hiring decision or stage change was performed. Live expiry/source-revocation mutation scenarios were not repeated against the cloud; existing synthetic unit coverage remains separate evidence. See [deployment operations and cost assumptions](deployment.md).
