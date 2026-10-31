"""Server-configured specialist adapters. No fallback specialist implementation."""

import importlib
import inspect
import json
import re
from copy import deepcopy
from typing import Protocol

from .schemas import SafeError

ROLES = frozenset({"Filter", "Schedule", "Interview", "Report"})


class AgentAdapter(Protocol):
    async def invoke(self, assignment: dict, runtime) -> dict:
        """Return a workflow-contract handoff; runtime offers a bounded model gateway only."""


class AdapterRegistry:
    def __init__(self, adapters=None):
        self.adapters = dict(adapters or {})
        if set(self.adapters) - ROLES:
            raise ValueError("Unknown specialist adapter role")

    @classmethod
    def configured(cls, raw):
        if not raw:
            return cls()
        entries = json.loads(raw)
        if not isinstance(entries, dict) or set(entries) - ROLES:
            raise ValueError("Invalid specialist adapter configuration")
        adapters = {}
        for role, entrypoint in entries.items():
            if not isinstance(entrypoint, str) or not re.fullmatch(
                r"[A-Za-z_][\w.]*:[A-Za-z_]\w*", entrypoint
            ):
                raise ValueError("Adapter must be a server-configured module:factory")
            module, factory = entrypoint.split(":")
            adapter = getattr(importlib.import_module(module), factory)()
            if not inspect.iscoroutinefunction(getattr(adapter, "invoke", None)):
                raise ValueError("Adapter must implement async invoke(assignment, runtime)")
            adapters[role] = adapter
        return cls(adapters)

    async def invoke(self, role, assignment, runtime):
        adapter = self.adapters.get(role)
        if adapter is None:
            raise SafeError(
                "adapter_unavailable",
                f"The {role} adapter is not configured. Plug in this specialist to continue.",
                needs_input=True,
            )
        value = await adapter.invoke(deepcopy(assignment), runtime)
        if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 64000:
            raise SafeError(
                "invalid_handoff", "The specialist adapter returned an invalid handoff."
            )
        return value

    def status(self):
        return {
            role: "configured" if role in self.adapters else "unavailable" for role in sorted(ROLES)
        }

    async def close(self):
        for adapter in self.adapters.values():
            if inspect.iscoroutinefunction(getattr(adapter, "close", None)):
                await adapter.close()
