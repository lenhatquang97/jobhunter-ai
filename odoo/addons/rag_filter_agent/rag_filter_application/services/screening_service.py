from ..dto.requests import ScreeningRequest
from ..dto.responses import (
    CandidateResultResponse,
    RequirementResponse,
    ScreeningResponse,
)


class ScreeningService:
    """Application use case; the ranking engine is injected by infrastructure."""

    def __init__(self, ranker):
        self._ranker = ranker

    def execute(self, request: ScreeningRequest) -> ScreeningResponse:
        requirements, ranked = self._ranker(
            request.model,
            (request.jd_filename, request.jd_content),
            [(cv.filename, cv.content) for cv in request.candidates],
        )
        requirement_dtos = tuple(
            RequirementResponse(name=item['name'], priority=item['priority'])
            for item in requirements['requirements']
        )
        result_dtos = tuple(
            CandidateResultResponse(
                rank=rank,
                filename=result['file'],
                candidate=result.get('candidate', ''),
                score=result.get('score', 0),
                summary=result.get('summary', ''),
                items=tuple(result.get('items', ())),
                error=result.get('error', ''),
            )
            for rank, result in enumerate(ranked, 1)
        )
        return ScreeningResponse(
            job=requirements.get('job', ''),
            requirements=requirement_dtos,
            results=result_dtos,
        )
