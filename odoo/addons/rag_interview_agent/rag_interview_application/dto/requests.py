from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IndexRequest:
    documents: dict[str, str]
    embedding_model: str

    def __post_init__(self):
        if not self.documents:
            raise ValueError('documents are required')
        if not self.embedding_model:
            raise ValueError('embedding_model is required')


@dataclass(frozen=True)
class InterviewTurnRequest:
    config: dict[str, str]
    context: dict[str, Any]
    index: list[dict[str, Any]]
    transcript: list[dict[str, Any]]
    asked: dict[str, Any] | None = None
    answer: str | None = None


@dataclass(frozen=True)
class ReportRequest:
    model: str
    context: dict[str, Any]
    documents: dict[str, str]
    transcript: list[dict[str, Any]]
    flags: str


@dataclass(frozen=True)
class RealtimeSecretRequest:
    model: str
    voice: str
    transcribe_model: str
    language: str = 'en'
