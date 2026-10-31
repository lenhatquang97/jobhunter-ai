FROM ghcr.io/astral-sh/uv:0.9.7 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/slack_bot UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra slack --no-dev
COPY slack_bot/jobhunter_slack ./slack_bot/jobhunter_slack
RUN useradd --create-home --uid 10002 jobhunter-slack
USER jobhunter-slack
CMD ["/app/.venv/bin/python", "-m", "jobhunter_slack"]
