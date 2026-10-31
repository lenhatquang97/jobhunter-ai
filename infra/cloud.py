"""Run a command with the scoped short-lived AWS session without shell exports."""

import json
import os
import subprocess
import sys
from pathlib import Path

state = Path(__file__).resolve().parents[1] / ".state"
credentials = json.loads((state / "aws-session.json").read_text())
env = dict(
    os.environ,
    AWS_ACCESS_KEY_ID=credentials["AccessKeyId"],
    AWS_SECRET_ACCESS_KEY=credentials["SecretAccessKey"],
    AWS_SESSION_TOKEN=credentials["SessionToken"],
    AWS_DEFAULT_REGION="us-east-1",
    AWS_REGION="us-east-1",
    AWS_PAGER="",
)
raise SystemExit(subprocess.call(sys.argv[1:], env=env))
