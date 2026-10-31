from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateDocumentRequest:
    filename: str
    content: bytes


@dataclass(frozen=True)
class ScreeningRequest:
    jd_filename: str
    jd_content: bytes
    candidates: tuple[CandidateDocumentRequest, ...]
    model: str

    def __post_init__(self):
        if not self.jd_content:
            raise ValueError('JD content is required')
        if not self.candidates:
            raise ValueError('at least one candidate CV is required')
        if not self.model:
            raise ValueError('model is required')
