"""Single-task supervisor. Secrets never appear in argv, config logs, or public events."""

import configparser
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg2
from psycopg2 import sql

config = json.loads(os.environ.pop("RUNTIME_CONFIG"))
config.update(json.loads(os.environ.pop("INIT_CONFIG", "{}")))
master = json.loads(os.environ.pop("MASTER_CONFIG", "{}"))
host = os.environ["DB_HOST"]
public_url = os.environ["PUBLIC_URL"].rstrip("/")
assert public_url.startswith("https://") and urlsplit(public_url).netloc.endswith(".chatgpt.site")
ca = "/etc/ssl/certs/aws-rds-global.pem"
os.environ["PGSSLROOTCERT"] = ca
os.environ["PGSSLMODE"] = "verify-full"


def connection(database, user, password):
    return psycopg2.connect(
        host=host,
        dbname=database,
        user=user,
        password=password,
        sslmode="verify-full",
        sslrootcert=ca,
        connect_timeout=15,
    )


def write_config():
    parser = configparser.ConfigParser(interpolation=None)
    parser["options"] = {
        "admin_passwd": config["db_manager_password"],
        "db_host": host,
        "db_port": "5432",
        "db_user": config["odoo_user"],
        "db_password": config["odoo_password"],
        "db_sslmode": "verify-full",
        "db_name": config["odoo_db"],
        "dbfilter": "^" + config["odoo_db"] + "$",
        "list_db": "False",
        "addons_path": "/opt/addons,/usr/lib/python3/dist-packages/odoo/addons",
        "data_dir": "/var/lib/odoo",
        "proxy_mode": "True",
        "http_interface": "127.0.0.1",
        "http_port": "8068",
        "gevent_port": "8072",
        "workers": "1",
        "max_cron_threads": "0",
        "limit_memory_soft": "1073741824",
        "limit_memory_hard": "1342177280",
        "limit_time_cpu": "180",
        "limit_time_real": "240",
        "log_level": "warn",
        "log_handler": ":WARNING,odoo.http:ERROR,werkzeug:ERROR",
        "without_demo": "True",
        "unaccent": "True",
        "smtp_server": "127.0.0.1",
        "smtp_port": "9",
    }
    path = Path("/tmp/jobhunter/odoo.conf")
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    with path.open("w") as handle:
        parser.write(handle)
    os.environ["JOBHUNTER_API_URL"] = "http://127.0.0.1:8000"
    os.environ["JOBHUNTER_BRIDGE_SECRET"] = config["bridge_secret"]
    os.environ.pop("JOBHUNTER_TEST_CAPTURE", None)
    return path


def initialize():
    assert master and config["odoo_db"] == "jobhunter_cloud"
    try:
        unexpected = psycopg2.connect(
            host="wrong-hostname.invalid",
            hostaddr=socket.gethostbyname(host),
            dbname="postgres",
            user=master["username"],
            password=master["password"],
            sslmode="verify-full",
            sslrootcert=ca,
            connect_timeout=15,
        )
    except psycopg2.OperationalError as error:
        assert "does not match host name" in str(error), "TLS negative check failed unexpectedly"
    else:
        unexpected.close()
        raise RuntimeError("TLS hostname verification was bypassed")
    conn = connection("postgres", master["username"], master["password"])
    conn.autocommit = True
    with conn.cursor() as cursor:
        for prefix in ("odoo", "api"):
            user, password, database = (
                config[f"{prefix}_{key}"] for key in ("user", "password", "db")
            )
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (user,))
            if not cursor.fetchone():
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                    ).format(sql.Identifier(user), sql.Literal(password))
                )
            cursor.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(user), sql.Identifier(master["username"])
                )
            )
            cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,))
            if not cursor.fetchone():
                cursor.execute(
                    sql.SQL(
                        "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8'"
                    ).format(sql.Identifier(database), sql.Identifier(user))
                )
    conn.close()
    for prefix in ("odoo", "api"):
        conn = connection(config[f"{prefix}_db"], master["username"], master["password"])
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
            cursor.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
            assert cursor.fetchone()[0]
        conn.close()
    path = write_config()
    subprocess.run(
        [
            "odoo",
            "--config",
            str(path),
            "--init",
            "t10_jobhunter_chat,hr_recruitment,mail_bot",
            "--stop-after-init",
            "--workers",
            "0",
            "--no-http",
        ],
        check=True,
    )
    seed_env = dict(os.environ)
    seed_env["SEED_CONFIG"] = json.dumps({k: config[k] for k in ("admin_password", "recruiters")})
    with open("/opt/infra/seed.py") as seed:
        subprocess.run(
            ["odoo", "shell", "--config", str(path), "--no-http"],
            stdin=seed,
            env=seed_env,
            check=True,
        )
    print("Fresh synthetic database initialized; TLS verified.")


def serve():
    path = write_config()
    env = dict(os.environ)
    env.update(
        JOBHUNTER_DATABASE_URL=f"postgresql+asyncpg://{config['api_user']}:{quote(config['api_password'], safe='')}@{host}:5432/{config['api_db']}",
        JOBHUNTER_DATABASE_SSL_MODE="verify-full",
        JOBHUNTER_DATABASE_SSL_CA=ca,
        JOBHUNTER_ODOO_BASE_URL="http://127.0.0.1:8072",
    )
    for key in (
        "JOBHUNTER_OPENAI_API_KEY",
        "JOBHUNTER_MODEL",
        "JOBHUNTER_REASONING_EFFORT",
        "JOBHUNTER_AGENT_ADAPTERS",
    ):
        if key in config:
            env[key] = config[key]
    nginx = Path("/tmp/jobhunter/nginx.conf")
    nginx.write_text(
        """pid /tmp/jobhunter/nginx.pid;
error_log /dev/null crit;
events {worker_connections 512;}
http {
 access_log off;
 client_body_temp_path /tmp/jobhunter/client;
 proxy_temp_path /tmp/jobhunter/proxy;
 fastcgi_temp_path /tmp/jobhunter/fastcgi;
 uwsgi_temp_path /tmp/jobhunter/uwsgi;
 scgi_temp_path /tmp/jobhunter/scgi;
 map $http_upgrade $connection_upgrade {default upgrade; '' close;}
 server {
  listen 8069;
  server_name _;
  client_max_body_size 12m;
  location ~* ^/(web/(database|tests|debug|assets/debug)|xmlrpc|jsonrpc)(/|$) {return 404;}
  if ($arg_debug != '') {return 404;}
  location / {
   proxy_pass http://127.0.0.1:8068;
   proxy_set_header Host PUBLIC_HOST;
   proxy_set_header X-Forwarded-Host PUBLIC_HOST;
   proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-Port 443;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_redirect off;
   proxy_read_timeout 180s;
   proxy_buffering off;
  }
  location /websocket {
   proxy_pass http://127.0.0.1:8072;
   proxy_http_version 1.1;
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection $connection_upgrade;
   proxy_set_header Host PUBLIC_HOST;
   proxy_set_header X-Forwarded-Host PUBLIC_HOST;
   proxy_set_header X-Forwarded-Proto https;
   proxy_read_timeout 3600s;
  }
 }
}
""".replace("PUBLIC_HOST", urlsplit(public_url).netloc)
    )
    processes = [
        subprocess.Popen(
            [
                "/opt/api/.venv/bin/uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--workers",
                "1",
                "--no-access-log",
            ],
            env=env,
        ),
        subprocess.Popen(["odoo", "--config", str(path)]),
        subprocess.Popen(["nginx", "-c", str(nginx), "-g", "daemon off;"]),
    ]

    def stop(*_):
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while all(p.poll() is None for p in processes):
        time.sleep(1)
    stop()
    for process in processes:
        try:
            process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
    sys.exit(1)


def install_ui():
    # Maintenance only: use the existing restricted Odoo database owner.
    # Never reinitialize the database, seed records, or start cron/SMTP.
    path = write_config()
    subprocess.run(
        ["odoo", "--config", str(path), "--init", "aho_recruitment_ui",
         "--stop-after-init", "--workers", "0", "--no-http"],
        check=True,
    )
    print("Recruitment UI installed; legacy RAG addons remain excluded.")


if sys.argv[1:] == ["install-ui"]:
    install_ui()
elif sys.argv[1:] == ["init"]:
    initialize()
else:
    serve()
