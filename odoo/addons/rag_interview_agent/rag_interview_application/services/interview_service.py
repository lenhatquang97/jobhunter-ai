from ..dto.requests import IndexRequest, InterviewTurnRequest, ReportRequest, RealtimeSecretRequest
from ..dto.responses import IndexResponse, InterviewReportResponse, InterviewTurnResponse, RealtimeSecretResponse


class InterviewService:
    """Application use cases with infrastructure functions injected."""

    def __init__(self, indexer, turn_generator, report_generator, realtime_secret_generator):
        self._indexer = indexer
        self._turn_generator = turn_generator
        self._report_generator = report_generator
        self._realtime_secret_generator = realtime_secret_generator

    def build_index(self, request: IndexRequest) -> IndexResponse:
        return IndexResponse(tuple(self._indexer(request.documents, request.embedding_model)))

    def next_turn(self, request: InterviewTurnRequest) -> InterviewTurnResponse:
        return InterviewTurnResponse.from_dict(self._turn_generator(
            request.config, request.context, request.index, request.transcript,
            request.asked, request.answer,
        ))

    def write_report(self, request: ReportRequest) -> InterviewReportResponse:
        return InterviewReportResponse.from_dict(self._report_generator(
            request.model, request.context, request.documents, request.transcript, request.flags,
        ))

    def realtime_secret(self, request: RealtimeSecretRequest) -> RealtimeSecretResponse:
        return RealtimeSecretResponse(self._realtime_secret_generator(
            request.model, request.voice, request.transcribe_model, request.language,
        ))
