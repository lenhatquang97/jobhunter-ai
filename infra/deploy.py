"""Apply pinned infrastructure, inject secrets out of band, initialize, then serve."""

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"


def cloud(*args):
    return subprocess.check_output(
        ["python3", str(ROOT / "infra/cloud.py"), *args], cwd=ROOT, text=True
    )


def aws(*args):
    return json.loads(cloud("aws", *args, "--output", "json"))


def apply():
    subprocess.run(
        [
            "python3",
            str(ROOT / "infra/cloud.py"),
            "terraform",
            "-chdir=infra/terraform",
            "apply",
            "-auto-approve",
            "-var-file=../../.state/terraform.tfvars.json",
        ],
        cwd=ROOT,
        check=True,
    )
    outputs = {
        k: v["value"]
        for k, v in json.loads(
            cloud("terraform", "-chdir=infra/terraform", "output", "-json")
        ).items()
    }
    (STATE / "cloud-outputs.json").write_text(json.dumps(outputs, indent=2))
    return outputs


def secret(arn, value):
    file = STATE / "secret-upload-private.json"
    file.touch(mode=0o600)
    file.chmod(0o600)
    file.write_text(json.dumps(value))
    try:
        aws(
            "secretsmanager",
            "put-secret-value",
            "--secret-id",
            arn,
            "--secret-string",
            "file://" + str(file),
        )
    finally:
        file.unlink()


def deploy():
    if (STATE / "hardened.json").exists():
        raise RuntimeError("Initialization is retired; use a normal Terraform maintenance apply")
    outputs = apply()
    private_path = STATE / "cloud-private.json"
    config = json.loads(private_path.read_text())
    runtime = {
        k: v
        for k, v in config.items()
        if k.startswith("JOBHUNTER_")
        or k
        in (
            "odoo_db",
            "odoo_user",
            "odoo_password",
            "api_db",
            "api_user",
            "api_password",
            "db_manager_password",
            "bridge_secret",
        )
    }
    secret(
        outputs["gateway_secret_arn"],
        {
            "origin_token": config["origin_token"],
            "public_url": config["public_url"],
            "cloudfront_url": outputs["cloudfront_url"],
        },
    )
    secret(outputs["runtime_secret_arn"], runtime)
    secret(
        outputs["bootstrap_secret_arn"], {k: config[k] for k in ("admin_password", "recruiters")}
    )
    config.update(
        cloudfront_url=outputs["cloudfront_url"],
        recruiter_login=config["recruiters"][0]["login"],
        recruiter_password=config["recruiters"][0]["password"],
    )
    private_path.write_text(json.dumps(config, indent=2))
    private_path.chmod(0o600)
    network = {
        "awsvpcConfiguration": {
            "subnets": [outputs["task_subnet"]],
            "securityGroups": [outputs["task_security_group"]],
            "assignPublicIp": "ENABLED",
        }
    }
    task = aws(
        "ecs",
        "run-task",
        "--cluster",
        "jobhunter",
        "--task-definition",
        outputs["init_task_definition"],
        "--launch-type",
        "FARGATE",
        "--platform-version",
        "1.4.0",
        "--network-configuration",
        json.dumps(network),
    )
    assert not task.get("failures"), "Initialization task rejected"
    arn = task["tasks"][0]["taskArn"]
    (STATE / "init-task.json").write_text(json.dumps({"arn": arn}))
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        current = aws("ecs", "describe-tasks", "--cluster", "jobhunter", "--tasks", arn)["tasks"][0]
        if current["lastStatus"] == "STOPPED":
            (STATE / "init-result.json").write_text(json.dumps(current, indent=2))
            assert current["containers"][0].get("exitCode") == 0, (
                "Initialization failed; inspect private init task logs"
            )
            break
        time.sleep(5)
    else:
        raise RuntimeError("Initialization did not finish within20minutes")
    variables = json.loads((STATE / "terraform.tfvars.json").read_text())
    variables["desired_count"] = 1
    (STATE / "terraform.tfvars.json").write_text(json.dumps(variables))
    apply()
    print("Cloud initialization succeeded; application desired count1.")


if __name__ == "__main__":
    deploy()
