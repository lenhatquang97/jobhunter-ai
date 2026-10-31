# Jobhunter AI interviews

React/Vite + FastAPI workspace containing two interview modes:

- AI screening: guided first-round questions grounded in company context, the JD, explicit must-have requirements, and the candidate CV, with English/Vietnamese typed answers or optional English voice. Drafts survive a refresh and stay editable until the candidate explicitly submits them. Recruiter notes use rubric and CV-consistency findings tied to stable submitted-answer IDs, without scores or automated recommendations.
- Human interview assistant: a prerecorded WAV is played as background audio and streamed in real time to AssemblyAI. AssemblyAI emits partial/final speaker-labelled turns and translates each final turn to Vietnamese through its LLM Gateway; GPT creates private follow-up suggestions and evidence-first feedback.

The assistant does not make hiring decisions or assign suitability scores. The WAV is only a deterministic demo source: transcript text is produced from the live AssemblyAI WebSocket stream, not replayed from a prepared transcript cache.

## Interview Agent boundary

The existing feature routes stay independent from orchestration. A future Orchestrator integrates through one bounded specialist endpoint:

```text
POST /api/agents/interview
Orchestrator assignment
  -> Interview Agent adapter
  -> persisted feature state
  -> Complete | Needs input | Failed handoff
```

The assignment carries `request_ref`, requested outcome (`prepare`, `conduct`, or `summarize`), harness version, candidate and approved role references, interview mode, source references/hashes, and an optional screening `session_id`. The response returns the shared top-level `assignment`, `context`, `result`, `summary`, `output`, `open_items`, and `next_action` fields. It preserves artifact/answer/finding references and a concrete next-action owner. It does not start a candidate conversation, call a model, dispatch another agent, change a candidate stage, or copy raw candidate answers into the handoff.

Current integration limits are explicit: quick-screening sessions can produce orchestration handoffs; prepared guides and human-assistant transcripts/feedback do not yet have persisted specialist artifacts, so those assignments return `Needs input` instead of simulated success.

## Schedule Agent boundary

The first scheduling slice is a resumable phone-screen invitation flow:

```text
Orchestrator supplies already-authorized candidate refs
  -> POST /api/agents/schedule
  -> read minimum candidate/job addressing data from Sea Postgres
  -> persist idempotent invitation + dry-run/Mailpit delivery
  -> pause as awaiting_response
  -> candidate accepts, declines, or reaches the deadline (ghosted)
  -> Orchestrator calls the same endpoint with requested_outcome=status
```

The Schedule Agent never decides that a candidate passed and never changes a remote candidate stage. `accepted` can complete the scheduling assignment. `declined` and `ghosted` are business outcomes returned as `Needs input` for the recruiter/Orchestrator; delivery/provider errors are `Failed`. Development delivery defaults to a local SQLite outbox. `mailpit` mode refuses non-local SMTP hosts.

The protected helper `GET /api/agents/schedule/demo-candidates` returns only deterministic screening-stage `@example.com` candidate references from the configured Sea database, not names, email addresses, or CV data. `GET /api/agents/schedule/outbox?request_ref=...` exposes PII-free response links for a recruiter testing the dry-run. Candidate responses use the tokenized `/r/schedule/{token}` page or `/api/schedule/respond/{token}` endpoint.

For an authorized live integration test, set `SCHEDULE_LIVE_EMAIL_ENABLED=true` and optionally `SCHEDULE_TEST_RECIPIENT` (otherwise `RECRUITER_EMAIL` is used), request `delivery_mode=smtp_test`, provide `interviewer_ref`, and set `require_live_availability=true`. Schedule rechecks both overlapping Sea interviews and Google FreeBusy before delivery. `smtp_test` always replaces the candidate address with the configured test inbox. Refresh Google Calendar consent through `GET /api/agents/schedule/google/connect/{interviewer_ref}` when the stored token lacks `calendar.freebusy`; the callback is `/api/google/callback`. This mode does not create a calendar event or change a candidate stage.

Onsite scheduling is the next slice, not silently implemented here: it will read assigned interviewers and busy intervals, propose timezone-explicit slots, let the candidate select one, recheck availability, and persist the provider-confirmed booking before returning `Complete`.

## Run locally

Backend (Python 3.12+):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` for AI screening or `/copilot` for the human interview assistant. Configure `RECRUITER_TOKEN`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, and `ASSEMBLYAI_API_KEY` in `backend/.env`. Schedule demo reads also require `DATABASE_URL`; `SCHEDULE_DB` defaults to `schedule.db`. Provider-backed flows require compatible account access; offline tests do not prove live provider access.

For the human interview demo, choose Strong or Weak, click **Connect AssemblyAI**, then **Start interview**. The browser resamples the audible WAV to 16 kHz PCM and streams it in real time. The demo initially maps AssemblyAI speakers A/B to interviewer/candidate, and the recruiter can correct that mapping in the UI.

## Verify

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm run lint
npm run build
```
