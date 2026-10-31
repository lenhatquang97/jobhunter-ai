import html
import json
import re
from pathlib import Path

import httpx

private = json.loads(Path(".state/cloud-private.json").read_text())
base = private["public_url"]


def login(user):
    client = httpx.Client(base_url=base, timeout=45, follow_redirects=False)
    response = client.get("/web/login")
    token = html.unescape(re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.text)[1])
    response = client.post(
        "/web/login",
        data={
            "login": user["login"],
            "password": user["password"],
            "csrf_token": token,
            "redirect": "/odoo/recruitment-applications",
        },
    )
    assert response.status_code in (302, 303)
    response = client.get("/odoo/recruitment-applications")
    token = html.unescape(re.search(r'csrf_token\s*:\s*["\x27]([^"\x27]+)', response.text)[1])
    return client, token


def rpc(client, csrf, operation, args):
    response = client.post(
        "/jobhunter/chat/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "call",
            "params": {"operation": operation, "arguments": args, "csrf_token": csrf},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]


first, csrf1 = login(private["recruiters"][0])
assert rpc(first, "", "conversations.list", {})["error"]["code"] == "csrf_failed"
conversations = rpc(first, csrf1, "conversations.list", {})["conversations"]
assert conversations
conversation = rpc(first, csrf1, "conversations.get", {"conversation_id": conversations[0]["id"]})
assert len([m for m in conversation["messages"] if m["role"] == "assistant"]) == 3
assert all(m.get("visibility") != "unavailable" for m in conversation["messages"])
assert all(r["source_state"] == "current" for r in conversation["runs"])
second, csrf2 = login(private["recruiters"][1])
denial = rpc(second, csrf2, "conversations.get", {"conversation_id": conversations[0]["id"]})
assert denial["error"]["code"] in ("not_found", "access_denied")
assert rpc(second, csrf2, "conversations.list", {})["conversations"] == []
invalid = first.get("/jobhunter/interview/" + "Z" * 43)
assert invalid.status_code == 404
first.close()
second.close()
result = {
    "chat_csrf_denied": True,
    "cross_recruiter_history_denied": True,
    "cross_recruiter_list_empty": True,
    "invalid_candidate_token_denied": True,
    "completed_history_answers": 3,
    "sources_current": True,
}
Path(".state/evidence/deployed-attempt2/acl-persistence.json").write_text(
    json.dumps(result, indent=2) + "\n"
)
print(json.dumps(result))
