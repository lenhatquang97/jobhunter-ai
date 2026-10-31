"""One-time root bootstrap. Credentials are written privately, never printed."""

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
STATE.mkdir(exist_ok=True, mode=0o700)
ACCOUNT = "511825855918"
REGION = "us-east-1"
ROLE = "jobhunter-deploy"
USER = "jobhunter-deploy-bootstrap"


def aws(*args, env=None):
    result = subprocess.run(
        ["aws", *args, "--region", REGION, "--output", "json"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def private(path, value):
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(value, indent=2))


def bootstrap():
    if (STATE / "hardened.json").exists():
        raise RuntimeError(
            "Deployment was finalized; obtain a newly authorized maintenance session instead of rerunning bootstrap"
        )
    identity = aws("sts", "get-caller-identity")
    assert identity["Account"] == ACCOUNT and identity["Arn"].endswith(":root")
    private_file = STATE / "bootstrap-private.json"
    if not private_file.exists():
        try:
            aws("iam", "get-user", "--user-name", USER)
        except subprocess.CalledProcessError:
            aws("iam", "create-user", "--user-name", USER)
            time.sleep(10)
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:user/{USER}"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        try:
            aws("iam", "get-role", "--role-name", ROLE)
        except subprocess.CalledProcessError:
            aws(
                "iam",
                "create-role",
                "--role-name",
                ROLE,
                "--max-session-duration",
                "43200",
                "--assume-role-policy-document",
                json.dumps(trust),
            )
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:*",
                        "ecs:*",
                        "elasticloadbalancing:*",
                        "elasticfilesystem:*",
                        "rds:*",
                        "ecr:*",
                        "logs:*",
                        "secretsmanager:*",
                        "ssmmessages:*",
                        "backup:*",
                        "backup-storage:MountCapsule",
                    ],
                    "Resource": "*",
                    "Condition": {"StringEquals": {"aws:RequestedRegion": REGION}},
                },
                {"Effect": "Allow", "Action": ["cloudfront:*", "budgets:*"], "Resource": "*"},
                {
                    "Effect": "Allow",
                    "Action": [
                        "kms:DescribeKey",
                        "kms:CreateGrant",
                        "kms:RetireGrant",
                        "kms:GenerateDataKey",
                        "kms:Decrypt",
                    ],
                    "Resource": f"arn:aws:kms:{REGION}:{ACCOUNT}:key/*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:*"],
                    "Resource": [
                        f"arn:aws:s3:::jobhunter-tfstate-{ACCOUNT}",
                        f"arn:aws:s3:::jobhunter-tfstate-{ACCOUNT}/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "iam:CreateRole",
                        "iam:DeleteRole",
                        "iam:GetRole",
                        "iam:UpdateAssumeRolePolicy",
                        "iam:PutRolePolicy",
                        "iam:GetRolePolicy",
                        "iam:DeleteRolePolicy",
                        "iam:ListRolePolicies",
                        "iam:ListAttachedRolePolicies",
                        "iam:TagRole",
                        "iam:UntagRole",
                        "iam:PassRole",
                    ],
                    "Resource": f"arn:aws:iam::{ACCOUNT}:role/jobhunter-runtime-*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["iam:CreateServiceLinkedRole"],
                    "Resource": "*",
                    "Condition": {
                        "StringLike": {
                            "iam:AWSServiceName": [
                                "ecs.amazonaws.com",
                                "rds.amazonaws.com",
                                "elasticloadbalancing.amazonaws.com",
                                "vpcorigin.cloudfront.amazonaws.com",
                                "backup.amazonaws.com",
                                "elasticfilesystem.amazonaws.com",
                            ]
                        }
                    },
                },
            ],
        }
        aws(
            "iam",
            "put-role-policy",
            "--role-name",
            ROLE,
            "--policy-name",
            "jobhunter-deployment",
            "--policy-document",
            json.dumps(policy),
        )
        aws(
            "iam",
            "put-user-policy",
            "--user-name",
            USER,
            "--policy-name",
            "AssumeDeploymentOnly",
            "--policy-document",
            json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "sts:AssumeRole",
                            "Resource": f"arn:aws:iam::{ACCOUNT}:role/{ROLE}",
                        }
                    ],
                }
            ),
        )
        key = aws("iam", "create-access-key", "--user-name", USER)["AccessKey"]
        private(
            private_file,
            {"AccessKeyId": key["AccessKeyId"], "SecretAccessKey": key["SecretAccessKey"]},
        )
        time.sleep(10)
    key = json.loads(private_file.read_text())
    env = dict(
        os.environ,
        AWS_ACCESS_KEY_ID=key["AccessKeyId"],
        AWS_SECRET_ACCESS_KEY=key["SecretAccessKey"],
    )
    env.pop("AWS_SESSION_TOKEN", None)
    creds = aws(
        "sts",
        "assume-role",
        "--role-arn",
        f"arn:aws:iam::{ACCOUNT}:role/{ROLE}",
        "--role-session-name",
        "jobhunter-publication",
        "--duration-seconds",
        "43200",
        env=env,
    )["Credentials"]
    private(STATE / "aws-session.json", creds)
    print("Scoped deployment session saved privately.")


if __name__ == "__main__":
    bootstrap()
