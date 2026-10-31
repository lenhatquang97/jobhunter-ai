"""Live HTTP boundary checks against the published Sites application.

Usage: uv run python e2e/check_deployment.py https://CLOUDFRONT_DOMAIN
Uses only dedicated synthetic recruiter credentials in the private handoff.
"""

import html
import json
import re
import sys
from pathlib import Path

import httpx
from websockets.sync.client import connect


def main():
    private = json.loads(Path(".state/cloud-private.json").read_text())
    base = private["public_url"].rstrip("/")
    result = {}
    with httpx.Client(base_url=base, timeout=45, follow_redirects=False) as client:
        root = client.get("/")
        assert (
            root.status_code == 302 and root.headers["location"] == "/odoo/recruitment-applications"
        ), "Native root entry failed"
        result["native_entry"] = True
        for path in (
            "/web/database/manager",
            "/web/tests",
            "/xmlrpc/db",
            "/xmlrpc/2/db",
            "/jsonrpc",
            "/odoo?debug=assets",
        ):
            assert client.get(path).status_code == 403, "Management/development route was reachable"
        result["management_routes_denied"] = True
        response = client.get("/web/login", params={"redirect": "/odoo/recruitment-applications"})
        assert response.status_code == 200, "Public login page failed"
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.text)
        assert match, "Odoo CSRF token missing"
        session_cookies = [
            cookie
            for cookie in response.headers.get_list("set-cookie")
            if cookie.startswith("session_id=")
        ]
        assert session_cookies and all(
            "secure" in value.lower() and "httponly" in value.lower() for value in session_cookies
        ), "Secure session cookie missing"
        result["secure_cookie_passthrough"] = True
        credentials = {
            "login": private["recruiter_login"],
            "password": private["recruiter_password"],
            "redirect": "/odoo/recruitment-applications",
        }
        rejected = client.post("/web/login", data=credentials)
        assert rejected.status_code == 400, "Missing CSRF token was accepted"
        result["csrf_denied"] = True
        authenticated = client.post(
            "/web/login", data=dict(credentials, csrf_token=html.unescape(match[1]))
        )
        assert authenticated.status_code in (302, 303), "Synthetic login rejected"
        assert ".cloudfront.net" not in authenticated.headers.get("location", ""), (
            "Origin leaked in login redirect"
        )
        app = client.get("/odoo/recruitment-applications")
        assert app.status_code == 200 and "web.assets" in app.text, (
            "Authenticated native app failed"
        )
        result["authenticated_native_app"] = True
        assert client.get("/v1/conversations").status_code == 404, "Internal API exposed publicly"
        result["internal_api_not_exposed"] = True
        cookie = "; ".join(f"{item.name}={item.value}" for item in client.cookies.jar)
        # Version read from the pinned, tested Odoo base image's bus handler.
        with connect(
            base.replace("https://", "wss://") + "/websocket?version=saas-19.5-1",
            origin=base,
            additional_headers={"Cookie": cookie},
            open_timeout=45,
        ) as socket:
            assert socket.response.status_code == 101, "WebSocket upgrade failed"
            assert socket.ping().wait(15), "End-to-end WebSocket pong missing"
            assert all(
                "cloudfront.net" not in item.lower()
                for item in socket.response.headers.get_all("set-cookie")
            ), "Origin-domain handshake cookie leaked"
        result["websocket_upgrade_and_pong"] = True
    if len(sys.argv) > 1:
        origin = httpx.get(
            sys.argv[1].rstrip("/") + "/web/login", timeout=45, follow_redirects=False
        )
        assert origin.status_code == 403, "CloudFront origin accepted an unauthenticated request"
        result["direct_origin_denied"] = True
    destination = Path(".state/evidence/deployed/http-boundaries.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
