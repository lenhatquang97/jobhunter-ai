"""Prepare synthetic interview WAV fixtures with AssemblyAI transcription and translation."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx


ASSEMBLYAI_API = "https://api.assemblyai.com"
SUPPORTED_FIXTURES = {"j1_strong", "j1_weak"}


class AssemblyAIDemoError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise AssemblyAIDemoError("ASSEMBLYAI_API_KEY is not configured")
    return {"authorization": api_key}


def _cache_path(demo_dir: Path, fixture: str, target_language: str) -> Path:
    safe_language = "".join(char for char in target_language.lower() if char.isalnum() or char == "-")
    return demo_dir / f"{fixture}.assemblyai.{safe_language}.json"


def _normalize(transcript: dict, target_language: str) -> dict:
    utterances = transcript.get("utterances") or []
    turns = []
    for index, utterance in enumerate(utterances):
        translated = utterance.get("translated_texts") or {}
        turns.append({
            "id": f"turn-{index}",
            "provider_speaker": str(utterance.get("speaker") or "?"),
            "text": str(utterance.get("text") or "").strip(),
            "translated_text": str(translated.get(target_language) or "").strip(),
            "start_ms": int(utterance.get("start") or 0),
            "end_ms": int(utterance.get("end") or 0),
        })
    turns = [turn for turn in turns if turn["text"]]
    if not turns:
        raise AssemblyAIDemoError("AssemblyAI returned no speaker-labelled utterances")
    return {
        "provider": "assemblyai",
        "transcript_id": transcript.get("id"),
        "target_language": target_language,
        "turns": turns,
    }


def prepare_fixture(
    demo_dir: Path,
    fixture: str,
    target_language: str = "vi",
    *,
    force: bool = False,
    poll_interval_seconds: float = 2.0,
    timeout_seconds: float = 300.0,
) -> dict:
    """Upload, transcribe, diarize, and translate one fixture, caching the normalized result."""
    if fixture not in SUPPORTED_FIXTURES:
        raise AssemblyAIDemoError("unknown demo fixture")
    if not target_language or len(target_language) > 12:
        raise AssemblyAIDemoError("invalid target language")
    wav_path = demo_dir / f"{fixture}.wav"
    if not wav_path.exists():
        raise AssemblyAIDemoError("demo WAV is missing")
    cache_path = _cache_path(demo_dir, fixture, target_language)
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    headers = _headers()
    deadline = time.monotonic() + timeout_seconds
    try:
        with httpx.Client(timeout=60) as client:
            with wav_path.open("rb") as audio:
                upload = client.post(f"{ASSEMBLYAI_API}/v2/upload", headers=headers, content=audio)
            upload.raise_for_status()
            audio_url = upload.json()["upload_url"]
            request = client.post(
                f"{ASSEMBLYAI_API}/v2/transcript",
                headers=headers,
                json={
                    "audio_url": audio_url,
                    "speech_models": ["universal-3-pro", "universal-2"],
                    "language_code": "en",
                    "speaker_labels": True,
                    "speakers_expected": 2,
                    "speech_understanding": {
                        "request": {
                            "translation": {
                                "target_languages": [target_language],
                                "formal": True,
                                "match_original_utterance": True,
                            }
                        }
                    },
                },
            )
            request.raise_for_status()
            transcript_id = request.json()["id"]
            while time.monotonic() < deadline:
                response = client.get(f"{ASSEMBLYAI_API}/v2/transcript/{transcript_id}", headers=headers)
                response.raise_for_status()
                transcript = response.json()
                if transcript.get("status") == "completed":
                    result = _normalize(transcript, target_language)
                    cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    return result
                if transcript.get("status") == "error":
                    raise AssemblyAIDemoError(str(transcript.get("error") or "AssemblyAI processing failed"))
                time.sleep(poll_interval_seconds)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise AssemblyAIDemoError(f"AssemblyAI request failed: {exc}") from exc
    raise AssemblyAIDemoError("AssemblyAI processing timed out")
