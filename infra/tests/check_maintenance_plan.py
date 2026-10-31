"""Validate Terraform JSON UI output for a normal immutable-image maintenance update.

Run against `terraform plan -json -var 'image=NEW_DIGEST'` output.
No plan/state file or AWS mutation is required.
"""

import json
import sys
from pathlib import Path

changes = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    event = json.loads(line)
    assert (
        event.get("type") != "diagnostic" or event.get("diagnostic", {}).get("severity") != "error"
    ), event
    if event.get("type") == "planned_change":
        change = event["change"]
        changes[change["resource"]["addr"]] = change["action"]

assert "aws_ecs_task_definition.init" not in changes, (
    "Retired bootstrap definition must remain frozen during an app image update",
    changes,
)
assert set(changes) == {"aws_ecs_task_definition.app", "aws_ecs_service.main"}, changes
assert changes["aws_ecs_task_definition.app"] == "replace", changes
assert changes["aws_ecs_service.main"] == "update", changes
print("Maintenance image plan changes app definition/service only; retired init remains unchanged.")
