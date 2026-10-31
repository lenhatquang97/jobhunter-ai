"""Prepare encrypted remote state and private runtime values using the deploy role."""

import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state = root / ".state"
if (state / "hardened.json").exists():
    raise RuntimeError(
        "Existing deployment is finalized; preserve its variables and use the maintenance workflow"
    )
credentials = json.loads((state / "aws-session.json").read_text())
env = dict(
    os.environ,
    AWS_ACCESS_KEY_ID=credentials["AccessKeyId"],
    AWS_SECRET_ACCESS_KEY=credentials["SecretAccessKey"],
    AWS_SESSION_TOKEN=credentials["SessionToken"],
    AWS_REGION="us-east-1",
    AWS_DEFAULT_REGION="us-east-1",
    AWS_PAGER="",
)


def aws(*args):
    r = subprocess.run(["aws", *args, "--output", "json"], env=env, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout) if r.stdout.strip() else {}


bucket = "jobhunter-tfstate-511825855918"
try:
    aws("s3api", "head-bucket", "--bucket", bucket)
except RuntimeError:
    aws("s3api", "create-bucket", "--bucket", bucket)
aws(
    "s3api",
    "put-public-access-block",
    "--bucket",
    bucket,
    "--public-access-block-configuration",
    json.dumps(
        dict(
            BlockPublicAcls=True,
            IgnorePublicAcls=True,
            BlockPublicPolicy=True,
            RestrictPublicBuckets=True,
        )
    ),
)
aws(
    "s3api",
    "put-bucket-encryption",
    "--bucket",
    bucket,
    "--server-side-encryption-configuration",
    json.dumps(
        {
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                    "BucketKeyEnabled": True,
                }
            ]
        }
    ),
)
aws(
    "s3api",
    "put-bucket-versioning",
    "--bucket",
    bucket,
    "--versioning-configuration",
    "Status=Enabled",
)
aws(
    "s3api",
    "put-bucket-policy",
    "--bucket",
    bucket,
    "--policy",
    json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DenyInsecureTransport",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                }
            ],
        }
    ),
)
(state / "backend.hcl").write_text(
    f'bucket = "{bucket}"\nkey = "jobhunter/terraform.tfstate"\nregion = "us-east-1"\nencrypt = true\nuse_lockfile = true\n'
)
private = state / "cloud-private.json"
if not private.exists():
    config = {
        "public_url": "https://jobhunter-workspace.netlink269.chatgpt.site",
        "origin_token": secrets.token_urlsafe(48),
        "odoo_db": "jobhunter_cloud",
        "odoo_user": "odoo_app",
        "odoo_password": secrets.token_urlsafe(36),
        "api_db": "jobhunter",
        "api_user": "jobhunter_app",
        "api_password": secrets.token_urlsafe(36),
        "admin_login": "admin",
        "admin_password": secrets.token_urlsafe(30),
        "db_manager_password": secrets.token_urlsafe(40),
        "bridge_secret": secrets.token_urlsafe(48),
        "recruiters": [
            {
                "login": f"workflow.qa{i + 1}",
                "name": f"QA Recruiter {i + 1} · Synthetic records",
                "password": secrets.token_urlsafe(24),
            }
            for i in range(2)
        ],
    }
    for line in (root / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if k in [
                "JOBHUNTER_OPENAI_API_KEY",
                "JOBHUNTER_MODEL",
                "JOBHUNTER_REASONING_EFFORT",
                "JOBHUNTER_AGENT_ADAPTERS",
            ]:
                config[k] = v.strip().strip('"').strip("'")
    assert config.get("JOBHUNTER_OPENAI_API_KEY")
    private.touch(mode=0o600)
    private.chmod(0o600)
    private.write_text(json.dumps(config, indent=2))
config = json.loads(private.read_text())
config["recruiter_login"] = config["recruiters"][0]["login"]
config["recruiter_password"] = config["recruiters"][0]["password"]
private.write_text(json.dumps(config, indent=2))
variables = {
    "public_url": config["public_url"],
    "origin_token_sha256": hashlib.sha256(config["origin_token"].encode()).hexdigest(),
    "image": "511825855918.dkr.ecr.us-east-1.amazonaws.com/jobhunter-runtime:bootstrap",
    "desired_count": 0,
    "runtime_boundary_enabled": False,
}
(state / "terraform.tfvars.json").write_text(json.dumps(variables))
print("Encrypted remote state and private runtime handoff prepared.")
