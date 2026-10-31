"""Migrated RAG specialists. Originals and provenance are kept under odoo/.

No Odoo ORM imports, credentials or provider clients live here. Each specialist
uses only the enclosing run's bounded runtime and returns a strict handoff.
"""

from .specialists import FilterAdapter, InterviewAdapter, ReportAdapter, ScheduleAdapter


def filter_adapter():
    return FilterAdapter()


def interview_adapter():
    return InterviewAdapter()


def schedule_adapter():
    return ScheduleAdapter()


def report_adapter():
    return ReportAdapter()
