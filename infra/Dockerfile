ARG ODOO_BASE
FROM ghcr.io/astral-sh/uv:0.9.7 AS uv
FROM ${ODOO_BASE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3.12-venv nginx ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /bin/uv
WORKDIR /opt/api
COPY pyproject.toml uv.lock ./
RUN uv venv --python /usr/bin/python3 /opt/api/.venv && uv sync --frozen --no-dev
COPY backend/app /opt/api/backend/app
COPY odoo/addons/t10_jobhunter_chat /opt/addons/t10_jobhunter_chat
COPY odoo/addons/aho_recruitment_ui /opt/addons/aho_recruitment_ui
COPY infra/runtime.py infra/health.py infra/seed.py infra/seed_demo.py infra/demo_data.py /opt/infra/
RUN curl -fsS https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o /etc/ssl/certs/aws-rds-global.pem && chmod 0644 /etc/ssl/certs/aws-rds-global.pem && mkdir -p /tmp/jobhunter && chown 100:101 /tmp/jobhunter
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/api/backend
USER odoo
ENTRYPOINT ["python3", "/opt/infra/runtime.py"]
CMD ["serve"]
