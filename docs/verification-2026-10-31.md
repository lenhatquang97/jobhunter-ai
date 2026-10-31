# Jobhunter Chat verification — 2026-10-31

Implemented and installed as a native addon in the existing local Odoo **20.0-20261015** app. Open **Recruitment → Jobhunter Chat**, or [the installed action](http://localhost:8069/odoo/action-277?db=t10). The Python service and its own PostgreSQL database run internally through the opt-in Odoo Compose overlay.

## Automated results

| Check | Observed result |
| --- | --- |
| Backend with disposable real PostgreSQL (`uv run python backend/scripts/test_with_postgres.py -q backend/tests`) | 69 passed |
| Whole repository (`uv run pytest -q`, including concurrent Slack work) | 114 passed, 1 optional PostgreSQL check skipped |
| Native Odoo addon ORM/controller suite on disposable database | 13 passed |
| Native Odoo Hoot component suite (`/web/tests?filter=t10_jobhunter_chat&debug=assets`) | 14 passed, zero browser console errors |
| Node UI boundary utilities | 5 passed |
| Harness behavior suite | 38 passed |
| Backend lint, JavaScript syntax, XML parsing, Compose configuration, and diff whitespace | Passed |

Counts overlap: the whole repository includes backend tests. The dedicated PostgreSQL run covers the optional database check separately. Automated provider tests use synthetic fakes; they are distinct from the real provider walkthroughs below.

## Actual browser and provider checks

Used a synthetic recruiter, job, applicant, and existing UTF-8 CV in `jobhunter_chat_synthetic_test`; no real candidate content was inspected.

- Real OpenAI: `get_job` → Orchestrator → Report produced an English role summary with a current Odoo citation.
- Real OpenAI: `read_document` → Orchestrator → Report answered in Vietnamese, preserved the candidate's self-report qualification, and cited exact CV lines 1–4, including an exact Vietnamese quotation.
- Expanded activity showed actual agent/tool events, validated inputs, timings, bounded source excerpts, and results. Document citations opened the normal Odoo content route.
- History reopened after backend container replacement without another model request. Source edits produced an outdated label; moving the synthetic source outside the recruiter's company scope hid the derived answer and tool details. Restoring access preserved unaffected document evidence.
- Stop returned the actual Stopped state; Archive removed the conversation from the active history list.
- At 390×844, document width remained 390 pixels and the composer fit without horizontal overflow. Desktop and narrow-layout screenshots were visually inspected.
- Disposable uninstall/reinstall preserved the synthetic job/applicant counts and document checksum. The addon was then installed in the existing `t10` database; only module metadata was changed there.

PDF/DOCX parsing, unsupported/scanned/encrypted documents, timeouts, restart interruption, permission denials, source injection, idempotency and late cancellation are covered by automated tests. Live document integration used TXT. This is a local development installation, with no AWS deployment or full recruiting workflow execution.

## Review and recovery

Independent backend, Odoo server, frontend, harness, and whole-change reviews were run. Material findings were reproduced and corrected, with regression checks for provisional-answer isolation, source-aware history, duplicate context normalization, grant cleanup, terminal event pagination, reconnect, New chat races, picker races, lost acknowledgments and history recovery. A live outdated-answer rendering issue was also reproduced in Hoot and fixed.

The service publishes answer text after final source/citation validation; agent/tool activity updates while work runs. Unvalidated partial model drafts stay internal and cannot enter trusted history or later prompts.

Sanitized command evidence, review reports, and screenshots are retained under the ignored application `.state/` and `.superpowers/` folders and referenced by the harness's scoped CHAT tasks. Credentials remain in ignored owner-only server configuration. Changes are uncommitted on `codex/jobhunter-chat`; concurrent Slack implementation was preserved.
