import os

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..rag_filter_application.dto.requests import CandidateDocumentRequest, ScreeningRequest
from ..rag_filter_application.services.screening_service import ScreeningService
from . import matcher

DEFAULT_MODEL = 'gpt-6.1-sol'  # ponytail: overridable in Settings (rag_filter.model); must accept PDF file inputs

class RagFilter(models.Model):
    _name = 'rag.filter'
    _description = 'CV Screening (JD match)'
    _order = 'id desc'

    name = fields.Char(required=True, default=lambda s: _('CV Screening'))
    jd_file = fields.Binary("JD (PDF)", required=True, attachment=True)
    jd_filename = fields.Char()
    cv_ids = fields.Many2many('ir.attachment', string="CVs (PDF)")
    job = fields.Char(readonly=True)
    requirements = fields.Text(readonly=True)
    result_ids = fields.One2many('rag.filter.result', 'filter_id', "Ranking", readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('done', 'Done')], default='draft')

    def _configure(self):
        # same raw read as rag_interview_agent: this Odoo build has no ir.config_parameter.get_param
        self.env.cr.execute("SELECT key, value FROM ir_config_parameter WHERE key IN ('rag_filter.openai_key', 'rag_filter.model')")
        params = dict(self.env.cr.fetchall())
        matcher.configure_key(params.get('rag_filter.openai_key') or os.environ.get('OPENAI_API_KEY', ''))
        return params.get('rag_filter.model') or DEFAULT_MODEL

    def action_match(self):
        self.ensure_one()
        cvs = [(a.name, a.raw.content) for a in self.cv_ids if a.mimetype == 'application/pdf' and a.raw]
        if not cvs:
            raise UserError(_("Upload at least one CV in PDF."))
        jd = self.jd_file
        if not jd or jd.mimetype != 'application/pdf':
            raise UserError(_("Upload the JD in PDF."))
        try:
            request = ScreeningRequest(
                jd_filename=self.jd_filename or 'job-description.pdf',
                jd_content=jd.content,
                candidates=tuple(CandidateDocumentRequest(name, content) for name, content in cvs),
                model=self._configure(),
            )
            response = ScreeningService(
                lambda model, jd_data, candidate_data: matcher.rank(model, jd_data, candidate_data)
            ).execute(request)
        except matcher.MatchError as e:
            raise UserError(str(e)) from e

        def lines(items, status):
            return '\n'.join(f"[{i['priority']}] {i['requirement']}: {i['evidence']}"
                             for i in items if i['status'] == status)

        self.result_ids.unlink()
        self.write({
            'job': response.job, 'state': 'done',
            'requirements': '\n'.join(f"[{r.priority}] {r.name}" for r in response.requirements),
            'result_ids': [(0, 0, {
                'rank': r.rank, 'file': r.filename, 'candidate': r.candidate, 'score': r.score,
                'summary': r.summary, 'error': r.error, 'details': list(r.items),
                'matched': lines(r.items, 'matched'), 'partial': lines(r.items, 'partial'),
                'missing': lines(r.items, 'missing'),
            }) for r in response.results],
        })

class RagFilterResult(models.Model):
    _name = 'rag.filter.result'
    _description = 'CV Screening Result'
    _order = 'rank'

    filter_id = fields.Many2one('rag.filter', required=True, ondelete='cascade', index=True)
    rank = fields.Integer()
    file = fields.Char("CV File")
    candidate = fields.Char()
    score = fields.Integer("Match %")
    summary = fields.Text()
    matched = fields.Text()
    partial = fields.Text()
    missing = fields.Text()
    details = fields.Json()
    error = fields.Text()
