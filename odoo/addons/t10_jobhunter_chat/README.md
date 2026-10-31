# Jobhunter Chat (native Odoo 20)

A Recruitment client action backed by the separate `jobhunter-ai` service. Odoo owns identity, company scope, permission checks and read-only business sources. This addon creates only private callback-grant metadata; it does not change jobs, applicants, stages or legacy automation.

## Local opt-in integration

From the `sea-hkt-t10` repository, copy `.env.jobhunter.example` to ignored `.env.jobhunter` and set independent random bridge/database secrets. Provider credentials stay server-side; a blank OpenAI key supports process/database readiness checks and fails chat execution explicitly. Use URL-safe characters for the PostgreSQL password.

```sh
docker compose --env-file .env --env-file .env.jobhunter -f docker-compose.yml -f docker-compose.jobhunter.yml config --quiet
docker compose --env-file .env --env-file .env.jobhunter -f docker-compose.yml -f docker-compose.jobhunter.yml up -d db odoo jobhunter-db jobhunter-api
```

Install `t10_jobhunter_chat` in a disposable synthetic database before trying Recruitment → Jobhunter Chat. The addon mount must be present when the Odoo process starts. After the disposable checks, install it in the existing app under the authorized application assignment. Keep the existing base `.env` when combining the overlay so legacy service configuration is preserved.

The API publishes no host port. Its PostgreSQL uses a separate internal network/volume; the API shares an isolated service network with Odoo, not Odoo's database network. The base Compose file and legacy addons are unchanged. `/health` performs no paid API call; `status: degraded` with `provider_configured: false` means storage/process are available but a provider key is missing.

Alternatively an Odoo administrator can set server-only `jobhunter_chat.api_url` and `jobhunter_chat.bridge_secret` configuration parameters. Environment variables `JOBHUNTER_API_URL` and `JOBHUNTER_BRIDGE_SECRET` take precedence. URLs and secrets never enter browser responses. Odoo's built-in database signing secret never leaves Odoo.

## Security and callback boundary

- Browser RPC `/jobhunter/chat/rpc` requires authenticated internal Recruitment membership, explicit `request.validate_csrf`, strict operations/arguments and validated native hyphen-separated `cids` cookie IDs. With no selection it uses only the user's main company. Superuser mode/UID1 and forged selections fail closed.
- Business reads use the original user with clean `allowed_company_ids` and `su=False`, standard record ACL/rules, field checks and an additional selected-company check. Context anchors are revalidated on every callback. Selected applicants do not grant sibling access; selected attachments do not grant sibling-document access. All document access checks the real parent even if the attachment is public. URL and protected-field attachments are denied.
- Callback `/jobhunter/chat/tool` is stateless JSON-RPC. Native Odoo routes `X-Odoo-Database` through `db_filter`; each request must also have the signed Bearer capability. Claims bind database UUID/name, user/companies, exact run or validation-scope UUID, purpose, tools, contexts and mandatory expiry ≤ five minutes. A private active grant record is checked each time. Original user membership is rechecked after reconstructing the environment.
- Execution capabilities include the six read tools plus server-only `validate_sources`. Fresh validation grants allow only source validation, including terminal history. BFF retrieval adds `X-Odoo-Validation-Run` for that grant's scope; service validates each historical run's source list independently through that scope. Cancellation commits revocation before calling the backend; terminal event polling also revokes execution grants. Expired grant metadata is removed by Odoo autovacuum after one day. Generic ORM users have own-read access only; grant reactivation is forbidden even through the model override.
- `sudo()` is narrowly limited to config/signing and grant metadata. No business data uses it. Callback errors are typed and omit raw provider/HTTP errors. Neither request headers nor source bodies are logged by this addon.

Sources contain allowlisted metadata or actual bounded record excerpts with UTC versions. Attachment bytes come from existing `raw` contents, bounded to 10 MiB. Extraction, strict citations, provider orchestration and persistent private chat history belong to the service. Source revocation/freshness is checked before reuse; the service masks derived output when access becomes unavailable.

## Verification and rollback

Runnable synthetic ORM/controller unit tests live in `tests/test_chat.py`. They do not call OpenAI. From this repository:

```sh
docker compose run --rm --no-deps \
  -v "$PWD/addons/t10_jobhunter_chat:/mnt/jobhunter-test-addons/t10_jobhunter_chat:ro" \
  odoo odoo server --addons-path=/mnt/jobhunter-test-addons,/mnt/extra-addons \
  -d jobhunter_chat_synthetic_test --db-filter='^jobhunter_chat_synthetic_test$' \
  -i t10_jobhunter_chat --test-tags=/t10_jobhunter_chat --stop-after-init \
  --no-http --workers=0 --max-cron-threads=0 --skip-auto-install \
  --data-dir=/tmp/jobhunter-chat-test-data
```

Use `-u` for subsequent checks. These are ORM tests and controller calls with a fake request, not an HTTP/browser suite. Browser/Hoot and real provider evidence must be reported separately. Tests use only newly created synthetic records and roll back fixture transactions.

For an authorized disposable-database rollback, uninstall this addon through Odoo. Its private grant metadata, action and menu are removed; shared recruitment models/records and the backend's separate persistent database remain. To stop local chat services, stop `jobhunter-api`/`jobhunter-db`; do not delete shared Odoo volumes. Existing legacy recruiting automation was not altered or disabled.
