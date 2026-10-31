"""Run tests against a fresh local PostgreSQL cluster and always stop/remove it.

Requires initdb/pg_ctl on PATH; never connects to an existing server or real records.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    for command in ("initdb", "pg_ctl"):
        if not shutil.which(command):
            raise SystemExit(f"{command} is required for disposable PostgreSQL verification")
    with tempfile.TemporaryDirectory(prefix="jobhunter-test-postgres-") as directory:
        cluster = str(Path(directory) / "data")
        log = str(Path(directory) / "postgres.log")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        subprocess.run(
            [
                "initdb",
                "-D",
                cluster,
                "-A",
                "trust",
                "-U",
                "jobhunter_test",
                "--no-locale",
                "--encoding=UTF8",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        started = False
        try:
            subprocess.run(
                [
                    "pg_ctl",
                    "-D",
                    cluster,
                    "-l",
                    log,
                    "-o",
                    f"-p {port} -h 127.0.0.1 -k {directory}",
                    "-w",
                    "start",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            started = True
            environment = {
                **os.environ,
                "JOBHUNTER_TEST_POSTGRES_URL": f"postgresql+asyncpg://jobhunter_test@127.0.0.1:{port}/postgres",
            }
            return subprocess.run(
                [sys.executable, "-m", "pytest", *sys.argv[1:]], env=environment
            ).returncode
        finally:
            if started:
                subprocess.run(
                    ["pg_ctl", "-D", cluster, "-m", "immediate", "-w", "stop"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )


if __name__ == "__main__":
    raise SystemExit(main())
