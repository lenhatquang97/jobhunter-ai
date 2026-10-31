"""FastAPI app: recruiter routes (create / list / view screening sessions) and candidate routes (token in the URL)."""
import hmac
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel

from . import agent, assemblyai_demo, copilot, store

_logger = logging.getLogger(__name__)

load_dotenv()

# ponytail: model ids from env so they can be swapped without a release.
DEFAULTS = {
    'turn_model': 'openai/gpt-4.1-mini',              # override after verifying the configured provider account
    'report_model': 'openai/gpt-4.1-mini',
    'embed_model': 'openai/text-embedding-3-large',   # multilingual retrieval, 3072 dims
    'realtime_model': 'gpt-realtime',
    'realtime_voice': 'marin',
    'transcribe_model': 'gpt-4o-transcribe',
}
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:5173')
MAX_CV = 5 * 1024 * 1024

def cfg(key):
    return os.environ.get(key.upper()) or DEFAULTS[key]

app = FastAPI(title='Jobhunter AI screening')
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get('CORS_ORIGINS', FRONTEND_URL).split(',')],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
store.init()
DEMO_DIR = Path(__file__).resolve().parents[1] / 'mock_data' / 'interview'
if DEMO_DIR.exists():
    app.mount('/api/copilot/demo', StaticFiles(directory=DEMO_DIR), name='copilot-demo')

# ------------------------------------------------------------------ recruiter side

def recruiter(authorization: str = Header('')):
    """ponytail: one shared RECRUITER_TOKEN; replace with the HR app's login when this is wired into it."""
    expected = os.environ.get('RECRUITER_TOKEN', '')
    if not expected or not hmac.compare_digest(authorization.removeprefix('Bearer '), expected):
        raise HTTPException(401, 'recruiter token required')

def cv_text(upload: UploadFile | None, typed: str) -> str:
    if not upload or not upload.filename:
        return typed.strip()
    raw = upload.file.read(MAX_CV + 1)
    if len(raw) > MAX_CV:
        raise HTTPException(422, 'CV larger than 5 MB')
    if upload.filename.lower().endswith('.pdf'):
        from pypdf import PdfReader
        try:
            return '\n\n'.join(p.extract_text() or '' for p in PdfReader(BytesIO(raw)).pages).strip()
        except Exception as e:  # noqa: BLE001 - a broken PDF is bad input, not a server error
            raise HTTPException(422, f'could not read PDF: {e}') from e
    if upload.filename.lower().endswith('.txt'):
        return raw.decode(errors='replace').strip()
    raise HTTPException(422, 'CV must be PDF or TXT')  # ponytail: add DOCX with python-docx when recruiters ask

def public(s, full=False):
    out = {k: s[k] for k in ('id', 'candidate', 'job', 'company', 'state', 'created_at', 'consent_at', 'error',
                             'max_topics', 'max_probes', 'flags', 'report')}
    out['url'] = f"{FRONTEND_URL}/interview/{s['token']}"
    if full:
        out['turns'] = s['turns']
    return out

@app.post('/api/interviews', dependencies=[Depends(recruiter)])
def create_interview(candidate: str = Form(...), job: str = Form(...), company: str = Form(...),
                     company_background: str = Form(''), job_description: str = Form(...),
                     cv: UploadFile | None = File(None), cv_plain: str = Form(''),
                     max_topics: int = Form(7), max_probes: int = Form(1)):
    docs = {'company': company_background or company, 'job_description': f'{job}\n\n{job_description}',
            'cv': cv_text(cv, cv_plain)}
    missing = [k for k, v in docs.items() if len(v.strip()) < 20]
    if missing:
        raise HTTPException(422, f"Missing input for the AI interview: {', '.join(missing)}")
    try:
        index = agent.build_index(docs, cfg('embed_model'))
    except agent.AgentError as e:
        raise HTTPException(502, f'Could not index the documents: {e}') from e
    for item in index:
        item['vec'] = [round(x, 5) for x in item['vec']]
    s = store.create(candidate=candidate, job=job, company=company, docs=docs, idx=index,
                     max_topics=max(1, min(12, max_topics)), max_probes=max(0, min(6, max_probes)))
    return public(s)

@app.get('/api/interviews', dependencies=[Depends(recruiter)])
def list_interviews():
    return [public(s) for s in store.all_()]

@app.get('/api/interviews/{id_}', dependencies=[Depends(recruiter)])
def get_interview(id_: int):
    s = store.get(id_)
    if not s:
        raise HTTPException(404)
    return public(s, full=True)

@app.post('/api/interviews/{id_}/retry-report', dependencies=[Depends(recruiter)])
def retry_report(id_: int, tasks: BackgroundTasks):
    s = store.get(id_)
    if not s or s['state'] not in ('done', 'error'):
        raise HTTPException(409, 'no finished interview to report on')
    store.update(id_, state='done', error=None)
    tasks.add_task(write_report, id_)
    return {'state': 'done'}

def write_report(id_):
    s = store.get(id_)
    try:
        rep = agent.report(cfg('report_model'), ctx(s), s['docs'], s['turns'], ', '.join(s['flags']))
    except agent.AgentError as e:
        _logger.warning('interview %s report failed: %s', id_, e)
        store.update(id_, state='error', error=str(e))
        return
    store.update(id_, state='reported', error=None, report=rep)

# ------------------------------------------------------------------ candidate side (token is the only credential)

def ctx(s):
    return {'job': s['job'], 'company': s['company'], 'max_topics': s['max_topics'], 'max_probes': s['max_probes']}

def session(token):
    s = store.by_token(token)
    if not s:
        raise HTTPException(404, 'closed')
    return s

def turn_cfg():
    return {'turn_model': cfg('turn_model'), 'embed_model': cfg('embed_model')}

def guarded(s, fn):
    """Public candidate route: always JSON, never a 500 with a stack trace."""
    try:
        with store.turn_lock(s['id']):
            return fn(store.get(s['id']))
    except store.Busy:
        return {'error': 'busy'}
    except agent.AgentError as e:
        _logger.warning('interview %s AI error: %s', s['id'], e)
        return {'error': 'ai', 'detail': str(e)[:240]}

@app.get('/api/i/{token}')
def candidate_page(token: str):
    s = store.by_token(token)
    return {'open': bool(s) and s['state'] in ('ready', 'running'),
            'job': s and s['job'], 'company': s and s['company'],
            'state': s and s['state'], 'pending': s and s['pending'],
            'turns': (s and s['turns']) or [], 'audio_consent': bool(s and s['consent_at'])}

class VoiceConsent(BaseModel):
    consent: bool = False


@app.post('/api/i/{token}/voice')
def voice(token: str, body: VoiceConsent):
    """Ephemeral OpenAI Realtime key + model for the browser's WebRTC call."""
    s = session(token)
    if s['state'] not in ('ready', 'running'):
        return {'error': 'closed'}
    if not body.consent:
        raise HTTPException(403, 'audio consent is required')
    store.update(s['id'], consent_at=store.now())
    try:
        secret = agent.realtime_secret(cfg('realtime_model'), cfg('realtime_voice'), cfg('transcribe_model'))
    except agent.AgentError as e:
        return {'error': 'ai', 'detail': str(e)[:240]}
    return {'secret': secret, 'model': cfg('realtime_model')}

def pending(res):
    return None if res['done'] else {'question': res['question'], 'topic': res['topic'], 'topic_no': res['topic_no']}

@app.post('/api/i/{token}/start')
def start(token: str):
    """Opening question, or the pending one again after a page reload."""
    def fn(s):
        if s['state'] == 'running' and s['pending']:
            return {'question': s['pending']['question'], 'done': False}
        if s['state'] != 'ready':
            return {'error': 'closed'}
        res = agent.next_turn(turn_cfg(), ctx(s), s['idx'], [])
        store.update(s['id'], pending=pending(res), state='running')
        return {'question': res['question'], 'done': res['done']}
    return guarded(session(token), fn)

class Answer(BaseModel):
    text: str = ''
    language: Literal['en', 'vi'] = 'en'

@app.post('/api/i/{token}/answer')
def answer(token: str, body: Answer, tasks: BackgroundTasks):
    text = body.text.strip()[:agent.MAX_ANSWER]
    if not text:
        return {'error': 'empty'}
    def fn(s):
        if s['state'] != 'running' or not s['pending']:
            return {'error': 'closed'}
        asked = s['pending']
        res = agent.next_turn(turn_cfg(), ctx(s), s['idx'], s['turns'], asked, text)
        turn_id = f"answer-{len(s['turns']) + 1}"
        submitted_turn = dict(asked, id=turn_id, answer=text, language=body.language,
                              submitted_at=store.now(), evidence=res['evidence'], flags=res['flags'])
        turns = s['turns'] + [submitted_turn]
        flags = sorted(set(s['flags']) | set(res['flags']))
        store.update(s['id'], turns=turns, flags=flags, pending=pending(res),
                     state='done' if res['done'] else 'running')
        if res['done']:
            tasks.add_task(write_report, s['id'])
        return {'question': res['question'], 'done': res['done'], 'submitted_turn': submitted_turn}
    return guarded(session(token), fn)


@app.post('/api/i/{token}/consent')
def consent(token: str, body: VoiceConsent):
    """Record explicit audio consent. Typed participation does not require it."""
    s = session(token)
    store.update(s['id'], consent_at=store.now() if body.consent else None)
    return {'audio_consent': body.consent}


@app.post('/api/i/{token}/cancel')
def cancel(token: str):
    s = session(token)
    store.update(s['id'], state='cancelled', pending=None)
    return {'state': 'cancelled'}


# ------------------------------------------------------------------ human interview copilot

ASSEMBLYAI_TOKEN_URL = 'https://streaming.assemblyai.com/v3/token'


@app.get('/api/copilot/assemblyai-token', dependencies=[Depends(recruiter)])
def assemblyai_token():
    """Return a short-lived streaming token; the provider key stays server-side."""
    api_key = os.environ.get('ASSEMBLYAI_API_KEY')
    if not api_key:
        raise HTTPException(503, 'ASSEMBLYAI_API_KEY is not configured')
    try:
        response = httpx.get(
            ASSEMBLYAI_TOKEN_URL,
            params={'expires_in_seconds': 60},
            headers={'Authorization': api_key},
            timeout=15,
        )
        response.raise_for_status()
        return {'token': response.json()['token']}
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(502, f'AssemblyAI token request failed: {exc}') from exc


@app.post('/api/copilot/prepare-demo/{fixture}', dependencies=[Depends(recruiter)])
def prepare_copilot_demo(fixture: str, target_language: str = 'vi', force: bool = False):
    """Prepare and cache a prerecorded fixture with AssemblyAI translation."""
    try:
        return assemblyai_demo.prepare_fixture(DEMO_DIR, fixture, target_language, force=force)
    except assemblyai_demo.AssemblyAIDemoError as exc:
        raise HTTPException(502, str(exc)) from exc


def copilot_guard(fn):
    try:
        return fn()
    except agent.AgentError as exc:
        raise HTTPException(502, str(exc)[:500]) from exc


@app.post('/api/copilot/follow-ups', dependencies=[Depends(recruiter)])
def copilot_follow_ups(body: copilot.TranscriptRequest):
    return copilot_guard(lambda: copilot.suggest_follow_ups(body))


@app.post('/api/copilot/translate', dependencies=[Depends(recruiter)])
def copilot_translate(body: copilot.TranslationRequest):
    return copilot_guard(lambda: copilot.translate_turn(body))


@app.post('/api/copilot/feedback', dependencies=[Depends(recruiter)])
def copilot_feedback(body: copilot.TranscriptRequest):
    return copilot_guard(lambda: copilot.build_feedback(body))
