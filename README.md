# Jobhunter AI interviews

React/Vite + FastAPI workspace containing two interview modes:

- AI screening: guided first-round questions with English/Vietnamese typed answers or optional English voice. Drafts survive a refresh and stay editable until the candidate explicitly submits them. Recruiter notes use rubric findings tied to stable submitted-answer IDs, without scores or automated recommendations.
- Human interview assistant: a prerecorded WAV is played as background audio and streamed in real time to AssemblyAI. AssemblyAI emits partial/final speaker-labelled turns and translates each final turn to Vietnamese through its LLM Gateway; GPT creates private follow-up suggestions and evidence-first feedback.

The assistant does not make hiring decisions or assign suitability scores. The WAV is only a deterministic demo source: transcript text is produced from the live AssemblyAI WebSocket stream, not replayed from a prepared transcript cache.

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

Open `http://localhost:5173` for AI screening or `/copilot` for the human interview assistant. Configure `RECRUITER_TOKEN`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, and `ASSEMBLYAI_API_KEY` in `backend/.env`. Provider-backed flows require compatible account access; offline tests do not prove live provider access.

For the human interview demo, choose Strong or Weak, click **Connect AssemblyAI**, then **Start interview**. The browser resamples the audible WAV to 16 kHz PCM and streams it in real time. The demo initially maps AssemblyAI speakers A/B to interviewer/candidate, and the recruiter can correct that mapping in the UI.

## Verify

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm run lint
npm run build
```
