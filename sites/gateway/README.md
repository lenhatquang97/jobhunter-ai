# Jobhunter Sites gateway

This dependency-free Worker serves the native Odoo application at the public Sites hostname. AWS hosts Odoo, the Python AI service, Aurora and EFS. Odoo remains responsible for sessions, CSRF, permissions and candidate links.

Runtime values, configured with native Sites tools:

- `PUBLIC_ORIGIN`: canonical public HTTPS Sites origin.
- `UPSTREAM_ORIGIN`: fixed HTTPS CloudFront origin.
- `ORIGIN_TOKEN`: private server-to-server origin credential, never browser code or source.

Run `npm test`, then `bash scripts/build.sh` and `node scripts/validate-artifact.mjs`. Publish through the Sites source helper using the project identity in `.openai/hosting.json`. Keep environment files, database contents and traces private.

Requests are forwarded once, with streamed responses, cookies, CSRF bodies and WebSocket upgrades. Untrusted forwarding metadata is replaced; cookies are secure and host-only. Shared caching and database management/development routes are disabled. Clients cannot choose an upstream.
