"""Pin the verified local ARM64 base, build and push the application image."""

import json
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state = root / ".state"
registry = "511825855918.dkr.ecr.us-east-1.amazonaws.com"


def cloud(*args):
    return subprocess.check_output(["python3", "infra/cloud.py", *args], cwd=root, text=True)


def run(*args):
    subprocess.run(args, cwd=root, check=True)


assert (
    json.loads(subprocess.check_output(["docker", "image", "inspect", "t10-odoo"], text=True))[0][
        "Id"
    ]
    == "sha256:231edfd90498a990dfbe24fbbdd778819e9c55ce67745f8af5b245bb57112c7b"
), "Local base changed since verification"
password = cloud("aws", "ecr", "get-login-password")
subprocess.run(
    ["docker", "login", "--username", "AWS", "--password-stdin", registry],
    input=password,
    text=True,
    check=True,
    stdout=subprocess.DEVNULL,
)
base = f"{registry}/jobhunter-odoo-base:tested-20.0-20261015-49529f"
try:
    base_digest = json.loads(
        cloud(
            "aws",
            "ecr",
            "describe-images",
            "--repository-name",
            "jobhunter-odoo-base",
            "--image-ids",
            "imageTag=tested-20.0-20261015-49529f",
        )
    )["imageDetails"][0]["imageDigest"]
except subprocess.CalledProcessError:
    run("docker", "tag", "t10-odoo", base)
    run("docker", "push", base)
    base_digest = json.loads(
        cloud(
            "aws",
            "ecr",
            "describe-images",
            "--repository-name",
            "jobhunter-odoo-base",
            "--image-ids",
            "imageTag=tested-20.0-20261015-49529f",
        )
    )["imageDetails"][0]["imageDigest"]
pinned = f"{registry}/jobhunter-odoo-base@{base_digest}"
image_tag = os.environ.get("JOBHUNTER_IMAGE_TAG", "publish-20261031-v1")
tag = f"{registry}/jobhunter-runtime:{image_tag}"
run(
    "docker",
    "build",
    "--platform",
    "linux/arm64",
    "--build-arg",
    f"ODOO_BASE={pinned}",
    "-f",
    "infra/Dockerfile",
    "-t",
    tag,
    ".",
)
run("docker", "push", tag)
digest = json.loads(
    cloud(
        "aws",
        "ecr",
        "describe-images",
        "--repository-name",
        "jobhunter-runtime",
        "--image-ids",
        f"imageTag={image_tag}",
    )
)["imageDetails"][0]["imageDigest"]
variables = json.loads((state / "terraform.tfvars.json").read_text())
variables["image"] = f"{registry}/jobhunter-runtime@{digest}"
(state / "terraform.tfvars.json").write_text(json.dumps(variables))
(state / "images.json").write_text(json.dumps({"base": pinned, "runtime": variables["image"]}))
print("Pinned base and production runtime pushed; digest saved.")
