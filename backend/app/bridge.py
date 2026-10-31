import asyncio
import json

import httpx

from .schemas import SafeError


class OdooBridge:
    def __init__(self, settings, client=None):
        self.timeout = settings.bridge_timeout_seconds
        self.url = settings.odoo_base_url.rstrip("/") + "/jobhunter/chat/tool"
        self.client = client or httpx.AsyncClient(
            timeout=settings.bridge_timeout_seconds, follow_redirects=False
        )

    async def close(self):
        await self.client.aclose()

    async def call(self, run_id, operation, arguments, capability, database):
        try:
            async with asyncio.timeout(self.timeout):
                return await self._request(run_id, operation, arguments, capability, database)
        except TimeoutError:
            raise SafeError("bridge_error", "Odoo is temporarily unavailable.") from None

    async def _request(self, run_id, operation, arguments, capability, database):
        try:
            async with self.client.stream(
                "POST",
                self.url,
                headers={"Authorization": "Bearer " + capability, "X-Odoo-Database": database},
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "id": run_id,
                    "params": {"run_id": run_id, "operation": operation, "arguments": arguments},
                },
            ) as response:
                if response.status_code != 200:
                    raise SafeError("bridge_error", "Odoo could not verify this request.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 15 * 1024 * 1024:
                        raise SafeError("invalid_tool_result")
            payload = json.loads(body)
            if (
                not isinstance(payload, dict)
                or "error" in payload
                or not isinstance(payload.get("result"), dict)
            ):
                raise SafeError("bridge_error", "Odoo could not verify this request.")
            result = payload["result"]
            if (
                type(result.get("ok")) is not bool
                or not isinstance(result.get("data"), dict)
                or not isinstance(result.get("sources"), list)
            ):
                raise SafeError("invalid_tool_result")
            return result
        except SafeError:
            raise
        except Exception as exc:
            raise SafeError("bridge_error", "Odoo is temporarily unavailable.") from exc
