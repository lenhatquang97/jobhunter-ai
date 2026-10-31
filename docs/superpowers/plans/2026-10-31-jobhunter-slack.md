# Jobhunter Slack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver a runnable local bilingual Slack interview-coordination pilot.

**Architecture:** Separate Bolt Socket Mode worker with its own SQLAlchemy/PostgreSQL
store, durable inbox/outbox, explicit role checks, versioned review cards and a
clock-driven scheduler. No existing Odoo API or business-tool changes.

**Tech Stack:** Python 3.12+, Pydantic, SQLAlchemy async/PostgreSQL, Slack Bolt,
OpenAI Responses, pytest, Docker Compose.

**Spec:** ../specs/2026-10-31-jobhunter-slack-design.md

## Global Constraints

- Separate DMs, one HR owner and one tech lead per interview; EN/VI per person.
- Asia/Ho_Chi_Minh, Mon–Fri09:00–18:00; four working-hour chase,24h/30m
  interview reminders, feedback at planned end and next working day09:00.
- No Odoo bridge access/business writes, calendar bookings, candidate outreach,
  ranking/scoring, feedback collection, commits, pushes or deployment.
- Tokens and candidate data never enter operational logs.

## Review Focus

- Untrusted/malformed AI output must not choose recipients or execute actions.
- Events, stale buttons and restarts must not recreate an obsolete schedule.
- Working-hour/weekend boundaries and delayed feedback delivery must stay correct.
- Delivery uncertainty must be visible without blind duplicate sends.
- Workspace/role/thread boundaries must protect private interview context.

## Task 1: Durable coordination and scheduling

**Files:** Create slack_bot/jobhunter_slack/{config,domain,store,coordinator}.py;
slack_bot/tests/test_coordination.py. Modify root pytest paths and optional dependencies.

**Interfaces:** Settings; Proposal/Intent Pydantic models; Store.initialize/close;
Coordinator.enqueue_message/enqueue_action/process_one/tick, with injected clock.

- [x] Write behavioral tests for approval, role boundaries, confirmation, revisions,
  cancellation, expiry, feedback, duplicate events and working-time deadlines.
- [x] Run `uv run --extra slack pytest slack_bot/tests/test_coordination.py -q` and observe RED.
- [x] Implement persistent state/inbox/outbox, version checks and deterministic deadlines.
- [x] Re-run task tests and existing suite; expected PASS.

## Task 2: Slack transport and bilingual AI

**Files:** Create slack_bot/jobhunter_slack/{provider,views,delivery,runtime,__main__}.py;
slack_bot/tests/test_transport.py.

**Interfaces:** Interpreter.parse(text, context, now)->Intent; Gateway active/open/post;
DeliveryWorker.deliver_one; Socket Mode listeners persist before processing.

- [x] Write tests for payload rendering, bilingual extraction, recipient constraints,
  failures, deduplication, missed reminders and uncertain sends; observe RED.
- [x] Implement structured Responses adapter, bilingual cards, safe Slack gateway,
  durable processing loops and single-worker PostgreSQL advisory lock.
- [x] Run task tests and whole suite; expected PASS.

## Task 3: Local setup and final validation

**Files:** Create slack_bot/{manifest.json,Dockerfile,README.md},
docker-compose.slack.yml and .env.slack.example; update README/.gitignore.

- [x] Supply installable manifest, isolated database/worker and local-secret instructions.
- [x] Run pytest, disposable PostgreSQL integration, Ruff, Compose configuration and
  worker preflight; verify existing backend source hashes remain unchanged.
- [x] Obtain a fresh code review, reproduce/fix important findings, and record evidence.
- [x] Run synthetic live Slack smoke only when installation, IDs and tokens are available;
  otherwise report this external prerequisite without claiming live success.

No commit steps: repository instructions prohibit commits without explicit assignment.
