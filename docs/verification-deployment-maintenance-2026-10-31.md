# AWS maintenance evidence — 2026-10-31

Target: existing Jobhunter AWS ECS service in us-east-1, behind the unchanged
Sites URL https://jobhunter-workspace.netlink269.chatgpt.site.

## Change

Built the current repository backend and the recruitment UI into the pinned
ARM64 runtime. The UI keeps its shared styles/sidebar, job cards and native
applicant views, and explicitly links Jobhunter Chat. Loaded UI data and models
no longer depend on legacy RAG voice/ranking modules. Existing typed workflows
continue through t10_jobhunter_chat. This deployment does not host the separate
React frontend or standalone Python entry point.

Added an explicit install-ui maintenance command using the existing restricted
Odoo database owner, no HTTP, no cron, no SMTP processing and no fixture seeding.
The retired bootstrap initializer remains unchanged. App task revisions are
retained with skip_destroy=true; no IAM policy or trust expansion was required.
A temporary bootstrap-user key was deleted immediately after obtaining the
short-lived scoped deployment session.

## Checks before the service switch

- Python suite: 194 passed, 1 PostgreSQL integration test skipped. Fakes and
  synthetic fixtures are unit evidence, not live-provider verification.
- Origin-gate Node suite: 3 passed. Infra Ruff, Terraform validation/formatting
  and Git whitespace checks passed.
- Whole-repository Ruff still reports 23 existing findings in standalone agent
  modules and tests. Those unrelated files were not changed for deployment.
- Disposable real Odoo/PostgreSQL: aho_recruitment_ui and t10_jobhunter_chat
  installed; neither imported RAG addon was present. Browser inspection
  confirmed the new sidebar/chat link, synthetic job card/funnel/warnings,
  applicant board and applicant form. The disposable UI had no backend service
  configured, so its chat error is not evidence about production chat.
- Independent code review: no actionable correctness or security findings.
- Existing deployed HTTP checks passed before maintenance: native entry/login,
  secure cookie passthrough, CSRF rejection, authenticated native app, hidden
  internal API, management/development denial, WebSocket 101/pong and direct
  CloudFront origin denial.
- Existing deployed isolation/history checks passed before maintenance:
  cross-recruiter access denied, invalid candidate token denied, three persisted
  assistant responses present and sources current.
- Aurora was available with encryption, deletion protection, seven-day backup
  retention and a recent restorable time. No restore drill was performed.

## Operational observations

The first image apply failed before service modification: the provider attempted
to deregister the old task definition using its family-only state ID and the
scoped role rejected the unscoped request. Applied a reviewed state-only retention
change using the old image, then re-planned. The new full maintenance plan changed
only aws_ecs_task_definition.app and aws_ecs_service.main; the initializer, database,
filestore, network, gateway and IAM resources were unchanged. Service tasks were
confirmed fully stopped, including STOPPING/DEACTIVATING states, before launching
the standalone UI install task. The restart plan changes only service count to 1.

## Verified after deployment

- UI maintenance task stopped with container exit code 0 and its explicit
  installation completion marker in CloudWatch.
- ECS service: desired 1, running 1, pending 0; task definition jobhunter:3.
  The app container and task are HEALTHY, and the sole ALB target is healthy.
- Running image matches the published immutable digest:
  `sha256:4405a1c6e7aae82bc7a20cd9daec248b2d1261c47a58f266a02bdf3d598963eb`.
- Full Terraform plan after restart reports no changes.
- Public HTTP boundary checks and WebSocket 101/pong all passed again.
- Read-only isolation/history checks all passed again, including the same three
  persisted assistant responses and current sources after task replacement.
- Authenticated synthetic-recruiter read checks confirmed new applicant views,
  the actual Recruitment action's new job cards, absent legacy RAG fields and
  successful downloads of the compiled new sidebar JavaScript/styles.
  The initial ad hoc view check used the generic HR job view and therefore did
  not contain recruitment cards; loading the actual Recruitment action resolved
  the correct view. This was a verification-selector issue, not an app change.
- Browser rendering/interactions were checked on the disposable instance;
  production UI evidence here consists of authenticated native view and compiled
  asset checks. No fresh provider generation or outreach was performed.
- Disposable local verification containers/network were removed. Existing local
  application containers were left untouched.

Private plans, credentials and task/log evidence remain ignored under .state;
no secrets are included in this report. Deployment includes the current source
at 0b5c2a5 plus the uncommitted compatibility/maintenance changes described above.
No source commits or pushes were made.


## Configuration update: high reasoning effort

User-requested follow-up: changed only JOBHUNTER_REASONING_EFFORT from low to
high in the AWS runtime secret and local ignored .env, preserving every other
runtime secret field. Forced an ECS service deployment with the existing
jobhunter:3 task definition and unchanged application image. The replacement
service reached rollout COMPLETED with desired/running 1 and pending 0, and its
task is HEALTHY. ECS Exec under the application user verified the running API
process environment contains JOBHUNTER_REASONING_EFFORT=high. Initial root-user
/proc inspection could not read the application's environment; inspection under
the application UID resolved that verification permission issue without changing
container permissions. Public authentication/security/WebSocket checks and the
read-only history/isolation checks passed again. No new provider generation was
performed and no business records were changed for verification.


## Responsive search border fix

Reproduced the Applications search field's light-gray border beside a black
secondary-button dropdown edge. The previous override applied only while the
field was focused. The dropdown now uses the field's border token in all states;
opening its menu also preserves that border on the field. Keyboard focus retains
a visible two-pixel outline. No search behavior, filters or record operations
were changed.

Verified in disposable real Odoo at 390, 768, 1024 and 1280 px widths, including
mobile search expansion/open filters and the Applications filter chip wrapping
to two lines. Visible field/dropdown borders resolve to rgb(228, 228, 228), no
horizontal overflow was observed, and typing still opens search suggestions.
Git whitespace checks passed. Disposable containers/network were removed.
