"""Migrated RAG specialists. Originals and provenance are kept under odoo/.

No Odoo ORM imports, credentials or provider clients live here. Each specialist
uses only the enclosing run's bounded runtime and returns a strict handoff.
"""

from .specialists import FilterAdapter, InterviewAdapter, ReportAdapter


def filter_adapter():
    return FilterAdapter()


def interview_adapter():
    return InterviewAdapter()


def report_adapter():
    return ReportAdapter()
