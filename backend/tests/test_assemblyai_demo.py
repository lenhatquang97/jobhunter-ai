"""Offline tests for normalized AssemblyAI demo output and cache reuse."""

import json

from app import assemblyai_demo


def test_normalize_preserves_speaker_timestamps_and_translation():
    result = assemblyai_demo._normalize({
        "id": "tx-1",
        "utterances": [{
            "speaker": "A", "text": "Hello", "start": 100, "end": 900,
            "translated_texts": {"vi": "Xin chào"},
        }],
    }, "vi")
    assert result["turns"] == [{
        "id": "turn-0", "provider_speaker": "A", "text": "Hello",
        "translated_text": "Xin chào", "start_ms": 100, "end_ms": 900,
    }]


def test_prepare_fixture_reuses_cache_without_provider_call(tmp_path, monkeypatch):
    (tmp_path / "j1_strong.wav").write_bytes(b"RIFF fixture")
    expected = {"provider": "assemblyai", "turns": [{"id": "turn-0"}]}
    (tmp_path / "j1_strong.assemblyai.vi.json").write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    assert assemblyai_demo.prepare_fixture(tmp_path, "j1_strong", "vi") == expected
