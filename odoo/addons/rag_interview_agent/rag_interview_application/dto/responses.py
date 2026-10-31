from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class IndexResponse:
    items: tuple[dict[str, Any], ...]

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class InterviewTurnResponse:
    question: str
    done: bool
    action: str
    topic: str
    topic_no: int
    score: int
    evidence: str
    flags: tuple[str, ...]

    @classmethod
    def from_dict(cls, value):
        return cls(
            question=value.get('question', ''), done=bool(value.get('done')),
            action=value.get('action', ''), topic=value.get('topic', ''),
            topic_no=int(value.get('topic_no') or 0), score=int(value.get('score') or 0),
            evidence=value.get('evidence', ''), flags=tuple(value.get('flags') or ()),
        )

    def to_public_dict(self):
        return {'question': self.question, 'done': self.done}


@dataclass(frozen=True)
class InterviewReportResponse:
    summary: str
    score: int
    recommendation: str
    strengths: tuple[str, ...]
    concerns: tuple[str, ...]
    cv_claims: tuple[str, ...]
    cv_consistency: str
    screening: tuple[str, ...]

    @classmethod
    def from_dict(cls, value):
        return cls(
            summary=value.get('summary', ''), score=max(0, min(100, int(value.get('score') or 0))),
            recommendation=value.get('recommendation', 'maybe'),
            strengths=tuple(value.get('strengths') or ()), concerns=tuple(value.get('concerns') or ()),
            cv_claims=tuple(value.get('cv_claims') or ()),
            cv_consistency=value.get('cv_consistency', 'minor_gaps'),
            screening=tuple(value.get('screening') or ()),
        )

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class RealtimeSecretResponse:
    secret: str

    def to_public_dict(self):
        return {'secret': self.secret}
