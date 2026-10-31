"""No credential-bearing health logs."""

import json
import urllib.request

for url in ("http://127.0.0.1:8000/health", "http://127.0.0.1:8069/web/health"):
    with urllib.request.urlopen(url, timeout=3) as response:
        assert response.status == 200
        if ":8000" in url:
            assert json.load(response)["database"] == "ok"
