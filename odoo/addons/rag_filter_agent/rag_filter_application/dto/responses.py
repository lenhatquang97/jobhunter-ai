from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RequirementResponse:
    name: str
    priority: str


@dataclass(frozen=True)
class CandidateResultResponse:
    rank: int
    filename: str
    candidate: str
    score: int
    summary: str
    items: tuple[dict[str, Any], ...]
    error: str = ''


@dataclass(frozen=True)
class ScreeningResponse:
    job: str
    requirements: tuple[RequirementResponse, ...]
    results: tuple[CandidateResultResponse, ...]

    def to_dict(self):
        return asdict(self)
