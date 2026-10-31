"""Root-only IAM bootstrap finalization; never uses root for workload operations.

Attach a bootstrap-owned ceiling to every runtime role and replace the temporary
provisioning policy with project-scoped maintenance permissions. Run after apply.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "511825855918"
REGION = "us-east-1"
BOUNDARY = f"arn:aws:iam::{ACCOUNT}:policy/jobhunter-runtime-boundary"


def aws(*args, scoped=False):
    command = ["aws", *args, "--region", REGION, "--output", "json"]
    if scoped:
        command = ["python3", str(ROOT / "infra/cloud.py"), *command]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def finalize():
    assert aws("sts", "get-caller-identity")["Arn"] == f"arn:aws:iam::{ACCOUNT}:root"
    cluster = aws(
        "rds", "describe-db-clusters", "--db-cluster-identifier", "jobhunter", scoped=True
    )["DBClusters"][0]
    assert cluster["MasterUserSecret"]["SecretArn"]
    efs = aws("efs", "describe-file-systems", scoped=True)["FileSystems"]
    efs_arn = next(
        item["FileSystemArn"]
        for item in efs
        if any(t["Key"] == "Project" and t["Value"] == "jobhunter" for t in item.get("Tags", []))
    )
    boundary = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken",
                    "ssmmessages:CreateControlChannel",
                    "ssmmessages:CreateDataChannel",
                    "ssmmessages:OpenControlChannel",
                    "ssmmessages:OpenDataChannel",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                ],
                "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/jobhunter-runtime",
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/jobhunter/runtime:*",
            },
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [
                    f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:jobhunter/runtime-*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "elasticfilesystem:ClientMount",
                    "elasticfilesystem:ClientWrite",
                    "elasticfilesystem:Backup",
                    "elasticfilesystem:DescribeFileSystems",
                    "elasticfilesystem:DescribeTags",
                ],
                "Resource": efs_arn,
            },
        ],
    }
    try:
        aws("iam", "get-policy", "--policy-arn", BOUNDARY)
    except subprocess.CalledProcessError:
        aws(
            "iam",
            "create-policy",
            "--policy-name",
            "jobhunter-runtime-boundary",
            "--policy-document",
            json.dumps(boundary),
        )
    else:
        aws(
            "iam",
            "create-policy-version",
            "--policy-arn",
            BOUNDARY,
            "--policy-document",
            json.dumps(boundary),
            "--set-as-default",
        )
    roles = [
        "jobhunter-runtime-execution",
        "jobhunter-runtime-init-execution",
        "jobhunter-runtime-task",
        "jobhunter-runtime-backup",
    ]
    for role in roles:
        aws(
            "iam",
            "put-role-permissions-boundary",
            "--role-name",
            role,
            "--permissions-boundary",
            BOUNDARY,
        )
    role_arns = [f"arn:aws:iam::{ACCOUNT}:role/{role}" for role in roles]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "RegionalReadOnly",
                "Effect": "Allow",
                "Action": [
                    "ec2:Describe*",
                    "ecs:Describe*",
                    "ecs:List*",
                    "rds:Describe*",
                    "rds:ListTagsForResource",
                    "elasticloadbalancing:Describe*",
                    "elasticfilesystem:Describe*",
                    "backup:Describe*",
                    "backup:Get*",
                    "backup:List*",
                    "secretsmanager:ListSecrets",
                    "logs:DescribeLogGroups",
                ],
                "Resource": "*",
                "Condition": {"StringEquals": {"aws:RequestedRegion": REGION}},
            },
            {
                "Sid": "TaggedEC2Maintenance",
                "Effect": "Allow",
                "Action": [
                    "ec2:Modify*",
                    "ec2:Delete*",
                    "ec2:AuthorizeSecurityGroup*",
                    "ec2:RevokeSecurityGroup*",
                    "ec2:CreateTags",
                    "ec2:DeleteTags",
                ],
                "Resource": f"arn:aws:ec2:{REGION}:{ACCOUNT}:*/*",
                "Condition": {"StringEquals": {"aws:ResourceTag/Project": "jobhunter"}},
            },
            {
                "Sid": "ECSProject",
                "Effect": "Allow",
                "Action": ["ecs:RegisterTaskDefinition"],
                "Resource": "*",
                "Condition": {"StringEquals": {"aws:RequestTag/Project": "jobhunter"}},
            },
            {
                "Sid": "ECSWorkload",
                "Effect": "Allow",
                "Action": [
                    "ecs:UpdateService",
                    "ecs:DeleteService",
                    "ecs:RunTask",
                    "ecs:StopTask",
                    "ecs:ExecuteCommand",
                    "ecs:DeregisterTaskDefinition",
                    "ecs:TagResource",
                    "ecs:UntagResource",
                ],
                "Resource": [
                    f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/jobhunter",
                    f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/jobhunter/*",
                    f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/jobhunter/*",
                    f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/jobhunter*:*",
                ],
            },
            {
                "Sid": "RDSProject",
                "Effect": "Allow",
                "Action": ["rds:Modify*", "rds:AddTagsToResource", "rds:RemoveTagsFromResource"],
                "Resource": [
                    f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:jobhunter",
                    f"arn:aws:rds:{REGION}:{ACCOUNT}:db:jobhunter-writer",
                    f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster-pg:jobhunter-postgres16",
                    f"arn:aws:rds:{REGION}:{ACCOUNT}:subgrp:jobhunter",
                ],
            },
            {
                "Sid": "ECRAuth",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Sid": "ECRProject",
                "Effect": "Allow",
                "Action": ["ecr:*"],
                "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/jobhunter-*",
            },
            {
                "Sid": "LogsProject",
                "Effect": "Allow",
                "Action": ["logs:*"],
                "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/jobhunter/*",
            },
            {
                "Sid": "RuntimeSecrets",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:UpdateSecret",
                    "secretsmanager:TagResource",
                    "secretsmanager:UntagResource",
                    "secretsmanager:GetResourcePolicy",
                ],
                "Resource": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:jobhunter/*",
            },
            {
                "Sid": "EFSProject",
                "Effect": "Allow",
                "Action": [
                    "elasticfilesystem:Update*",
                    "elasticfilesystem:PutBackupPolicy",
                    "elasticfilesystem:TagResource",
                    "elasticfilesystem:UntagResource",
                ],
                "Resource": efs_arn,
            },
            {
                "Sid": "State",
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucket*"],
                "Resource": f"arn:aws:s3:::jobhunter-tfstate-{ACCOUNT}",
            },
            {
                "Sid": "StateObjects",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::jobhunter-tfstate-{ACCOUNT}/jobhunter/*",
            },
            {
                "Sid": "ReadRuntimeRoles",
                "Effect": "Allow",
                "Action": [
                    "iam:GetRole",
                    "iam:GetRolePolicy",
                    "iam:ListRolePolicies",
                    "iam:ListAttachedRolePolicies",
                ],
                "Resource": role_arns,
            },
            {
                "Sid": "BoundedRuntimePolicies",
                "Effect": "Allow",
                "Action": [
                    "iam:PutRolePolicy",
                    "iam:DeleteRolePolicy",
                    "iam:TagRole",
                    "iam:UntagRole",
                ],
                "Resource": role_arns,
            },
            {
                "Sid": "KeepBoundary",
                "Effect": "Allow",
                "Action": ["iam:PutRolePermissionsBoundary"],
                "Resource": role_arns,
                "Condition": {"ArnEquals": {"iam:PermissionsBoundary": BOUNDARY}},
            },
            {
                "Sid": "PassECSTaskRoles",
                "Effect": "Allow",
                "Action": ["iam:PassRole"],
                "Resource": [role_arns[0], role_arns[2]],
                "Condition": {"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            },
            {
                "Sid": "PassBackupRole",
                "Effect": "Allow",
                "Action": ["iam:PassRole"],
                "Resource": role_arns[3],
                "Condition": {"StringEquals": {"iam:PassedToService": "backup.amazonaws.com"}},
            },
        ],
    }
    outputs = json.loads(
        subprocess.check_output(
            [
                "python3",
                str(ROOT / "infra/cloud.py"),
                "terraform",
                "-chdir=infra/terraform",
                "output",
                "-json",
            ],
            cwd=ROOT,
            text=True,
        )
    )
    distribution = outputs["distribution_id"]["value"]
    policy["Statement"] += [
        {
            "Sid": "CloudFrontRead",
            "Effect": "Allow",
            "Action": ["cloudfront:Get*", "cloudfront:List*", "cloudfront:Describe*"],
            "Resource": "*",
        },
        {
            "Sid": "CloudFrontProject",
            "Effect": "Allow",
            "Action": [
                "cloudfront:UpdateDistribution",
                "cloudfront:TagResource",
                "cloudfront:UntagResource",
                "cloudfront:CreateInvalidation",
            ],
            "Resource": f"arn:aws:cloudfront::{ACCOUNT}:distribution/{distribution}",
        },
        {
            "Sid": "OriginGate",
            "Effect": "Allow",
            "Action": [
                "cloudfront:UpdateFunction",
                "cloudfront:PublishFunction",
                "cloudfront:TestFunction",
            ],
            "Resource": f"arn:aws:cloudfront::{ACCOUNT}:function/jobhunter-origin-gate",
        },
        {
            "Sid": "BudgetProject",
            "Effect": "Allow",
            "Action": [
                "budgets:ViewBudget",
                "budgets:ModifyBudget",
                "budgets:ListTagsForResource",
                "budgets:TagResource",
                "budgets:UntagResource",
            ],
            "Resource": f"arn:aws:budgets::{ACCOUNT}:budget/jobhunter-monthly-100",
        },
    ]
    aws(
        "iam",
        "put-role-policy",
        "--role-name",
        "jobhunter-deploy",
        "--policy-name",
        "jobhunter-deployment",
        "--policy-document",
        json.dumps(policy, separators=(",", ":")),
    )
    (ROOT / ".state/final-deploy-policy.json").write_text(json.dumps(policy, indent=2))
    state = ROOT / ".state"
    variables = json.loads((state / "terraform.tfvars.json").read_text())
    variables["runtime_boundary_enabled"] = True
    (state / "terraform.tfvars.json").write_text(json.dumps(variables))
    key_file = state / "bootstrap-private.json"
    if key_file.exists():
        key = json.loads(key_file.read_text())
        aws(
            "iam",
            "update-access-key",
            "--user-name",
            "jobhunter-deploy-bootstrap",
            "--access-key-id",
            key["AccessKeyId"],
            "--status",
            "Inactive",
        )
        key_file.unlink()
    (state / "hardened.json").write_text(
        json.dumps({"boundary": BOUNDARY, "bootstrap_key": "disabled", "maintenance_roles": roles})
    )
    print("Runtime boundary attached; deploy role project-scoped; bootstrap key disabled.")


if __name__ == "__main__":
    finalize()
