import logging
import os
import secrets

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from ..rag_interview_application.dto.requests import IndexRequest, InterviewTurnRequest, ReportRequest, RealtimeSecretRequest
from ..rag_interview_application.services.interview_service import InterviewService
from . import agent

_logger = logging.getLogger(__name__)

# ponytail: model ids live in System Parameters (rag_interview.*) so they can be swapped without a release.
DEFAULTS = {
    'turn_model': 'openai/gpt-6.1-sol',               # live turn: sharpest follow-ups at voice-friendly latency
    'report_model': 'openai/gpt-6-astra',             # offline report: flagship reasoning, latency irrelevant
    'embed_model': 'openai/text-embedding-3-large',   # multilingual retrieval, 3072 dims
    'realtime_model': 'gpt-realtime',
    'realtime_voice': 'marin',
    'transcribe_model': 'gpt-4o-transcribe',
}

class ResCompany(models.Model):
    _inherit = 'res.company'

    rag_background = fields.Text("Company Background (AI Interview)",
                                 help="Mission, products, culture, team, ways of working: what the AI interviewer "
                                      "should know about the company.")

class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    rag_interview_ids = fields.One2many('rag.interview', 'applicant_id', "AI Interviews")

    def _rag_cv_text(self):
        """CV text from the newest attachment. attachment_indexation (a hr_recruitment dependency) already
        extracted text from PDF / DOCX / XLSX into index_content."""
        for att in self.attachment_ids.sorted('id', reverse=True):
            text = (att.sudo().index_content or '').strip()
            if not text and att.mimetype == 'application/pdf':
                try:
                    from io import BytesIO
                    from pypdf import PdfReader
                    text = '\n\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(att.raw or b'')).pages).strip()
                except Exception as e:  # noqa: BLE001 - attachment parsing must not break applicant access
                    _logger.warning("Could not extract PDF CV %s: %s", att.id, e)
            if not text and (att.mimetype or '').startswith('text/'):
                text = (att.raw or b'').decode(errors='replace').strip()
            if text:
                return text
        return ''

    def action_rag_interview(self):
        self.ensure_one()
        session = self.rag_interview_ids.filtered(lambda s: s.state in ('ready', 'running'))[:1] \
            or self.env['rag.interview'].create({'applicant_id': self.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'rag.interview', 'res_id': session.id,
                'view_mode': 'form', 'target': 'current'}

class RagInterview(models.Model):
    _name = 'rag.interview'
    _description = 'AI Interview (RAG)'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    applicant_id = fields.Many2one('hr.applicant', required=True, ondelete='cascade', index=True)
    job_id = fields.Many2one(related='applicant_id.job_id', store=True)
    company_id = fields.Many2one(related='applicant_id.company_id', store=True)
    token = fields.Char(copy=False, index=True, groups='hr_recruitment.group_hr_recruitment_user')
    url = fields.Char(compute='_compute_url')
    state = fields.Selection([('ready', 'Ready'), ('running', 'Interviewing'), ('done', 'Writing Report'),
                              ('reported', 'Reported'), ('error', 'Error')], default='ready', tracking=True)
    max_topics = fields.Integer("Max Topics", default=7,
                                help="Distinct topics the agent may open. Follow-up questions do not count.")
    max_probes = fields.Integer("Max Follow-ups per Topic", default=1,
                                help="How many times the agent may dig deeper into one topic before moving on.")
    docs = fields.Json(copy=False)      # {source: full text}: what the report model reads
    index = fields.Json(copy=False)     # [{source, text, vec}]: what retrieval reads
    pending_question = fields.Text(copy=False)
    pending_topic = fields.Char(copy=False)
    pending_topic_no = fields.Integer(copy=False)
    turn_ids = fields.One2many('rag.interview.turn', 'interview_id', "Transcript")
    flags = fields.Char(copy=False)
    consent_at = fields.Datetime(copy=False)
    score = fields.Integer(copy=False)
    recommendation = fields.Selection([('strong_yes', 'Strong Yes'), ('yes', 'Yes'), ('maybe', 'Maybe'),
                                       ('no', 'No')], copy=False, tracking=True)
    summary = fields.Text(copy=False)
    strengths = fields.Text(copy=False)
    concerns = fields.Text(copy=False)
    cv_claims = fields.Text("CV Claims Checked", copy=False)
    cv_consistency = fields.Selection([('consistent', 'Consistent'), ('minor_gaps', 'Minor Gaps'),
                                       ('inconsistent', 'Inconsistent')], "CV Consistency", copy=False, tracking=True)
    screening = fields.Text("Screening Answers", copy=False)
    error = fields.Text(copy=False)

    @api.depends('applicant_id', 'job_id')
    def _compute_name(self):
        for s in self:
            s.name = f"{s.applicant_id.partner_name or s.applicant_id.display_name} - {s.job_id.name or ''}"

    def _compute_url(self):
        for s in self:
            s.url = s.sudo().token and f"{s.get_base_url()}/rag-interview/{s.sudo().token}"

    @api.model
    def _cfg(self, key):
        self.env.cr.execute('SELECT value FROM ir_config_parameter WHERE key=%s', [f'rag_interview.{key}'])
        row = self.env.cr.fetchone()
        return row[0] if row and row[0] else DEFAULTS[key]

    def _configure_keys(self):
        self.env.cr.execute("SELECT key, value FROM ir_config_parameter WHERE key IN ('rag_interview.openrouter_key', 'rag_interview.openai_key')")
        params = dict(self.env.cr.fetchall())
        agent.configure_keys(params.get('rag_interview.openrouter_key') or os.environ.get('OPENROUTER_API_KEY', ''),
                             params.get('rag_interview.openai_key') or os.environ.get('OPENAI_API_KEY', ''))

    @staticmethod
    def _service():
        return InterviewService(agent.build_index, agent.next_turn, agent.report, agent.realtime_secret)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create([dict(v, token=secrets.token_urlsafe(32)) for v in vals_list])
        for s in records:
            s._build_index()
        return records

    def _build_index(self):
        """The three inputs, indexed once per candidate."""
        self._configure_keys()
        job, company = self.job_id, self.company_id
        docs = {
            'company': company.rag_background or company.name or '',
            'job_description': '\n\n'.join(filter(None, [job.name, html2plaintext(job.description or ''),
                                                         job.sudo().requirements or ''])),
            'cv': self.applicant_id._rag_cv_text(),
        }
        missing = [k for k, v in docs.items() if len(v.strip()) < 20]
        if missing:
            raise UserError(_("Missing input for the AI interview: %s. Fill the company background (Settings > "
                              "Companies), the job description, and attach the CV to the applicant.",
                              ', '.join(missing)))
        try:
            index = self._service().build_index(IndexRequest(docs, self._cfg('embed_model'))).items
        except agent.AgentError as e:
            raise UserError(_("Could not index the documents: %s", e)) from e
        index = list(index)
        for item in index:
            item['vec'] = [round(x, 5) for x in item['vec']]
        self.sudo().write({'docs': docs, 'index': index})

    def action_rebuild_index(self):
        for s in self.filtered(lambda s: s.state == 'ready'):
            s._build_index()

    # ------------------------------------------------------------------ candidate side (called with sudo)

    def _lock(self):
        """One turn at a time per session: a double submit must not ask two questions."""
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute('SELECT id FROM rag_interview WHERE id=%s FOR UPDATE NOWAIT', [self.id])
        except psycopg2.errors.LockNotAvailable:
            raise UserError(_("busy")) from None

    def _voice_secret(self):
        self._configure_keys()
        return self._service().realtime_secret(RealtimeSecretRequest(
            self._cfg('realtime_model'), self._cfg('realtime_voice'), self._cfg('transcribe_model'))).secret

    def _ctx(self):
        return {'job': self.job_id.name or '', 'company': self.company_id.name or '',
                'max_topics': max(1, min(12, self.max_topics)), 'max_probes': max(0, min(6, self.max_probes))}

    def _transcript(self):
        return [{'question': t.question, 'answer': t.answer, 'score': t.score, 'topic': t.topic,
                 'topic_no': t.topic_no} for t in self.turn_ids]

    def _pending(self, res):
        return {'pending_question': False if res['done'] else res['question'],
                'pending_topic': False if res['done'] else res['topic'],
                'pending_topic_no': 0 if res['done'] else res['topic_no']}

    def _start(self):
        """Opening question, or the pending one again after a page reload."""
        self._lock()
        self.invalidate_recordset()
        if self.state == 'running' and self.pending_question:
            return {'question': self.pending_question, 'done': False}
        if self.state != 'ready':
            raise UserError(_("closed"))
        res = self._service().next_turn(InterviewTurnRequest(
            self._turn_cfg(), self._ctx(), self.index, []))
        self.write(dict(self._pending(res.__dict__), state='running', consent_at=fields.Datetime.now()))
        return res.to_public_dict()

    def _turn_cfg(self):
        self._configure_keys()
        return {'turn_model': self._cfg('turn_model'), 'embed_model': self._cfg('embed_model')}

    def _answer(self, answer):
        self._lock()
        self.invalidate_recordset()
        if self.state != 'running' or not self.pending_question:
            raise UserError(_("closed"))
        answer = answer[:agent.MAX_ANSWER]
        asked = {'question': self.pending_question, 'topic': self.pending_topic or '',
                 'topic_no': self.pending_topic_no}
        res = self._service().next_turn(InterviewTurnRequest(
            self._turn_cfg(), self._ctx(), self.index, self._transcript(), asked, answer))
        self.env['rag.interview.turn'].create({
            'interview_id': self.id, 'question': self.pending_question, 'answer': answer,
            'topic': asked['topic'], 'topic_no': asked['topic_no'],
            'score': res.score, 'evidence': res.evidence, 'flags': ', '.join(res.flags)})
        flags = sorted(set(filter(None, (self.flags or '').split(', '))) | set(res.flags))
        self.write(dict(self._pending(res.__dict__), flags=', '.join(flags), state='done' if res.done else 'running'))
        if res.done:
            self.env.ref('rag_interview_agent.ir_cron_rag_report')._trigger()
        return res.to_public_dict()

    # ------------------------------------------------------------------ report

    @api.model
    def _cron_report(self):
        self._configure_keys()
        for s in self.search([('state', '=', 'done')], limit=5):
            try:
                rep = self._service().write_report(ReportRequest(
                    s._cfg('report_model'), s._ctx(), s.docs, s._transcript(), s.flags or ''))
            except agent.AgentError as e:
                _logger.warning("RAG interview %s report failed: %s", s.id, e)
                s.write({'state': 'error', 'error': str(e)})
                continue
            s.write({'state': 'reported', 'error': False, 'score': rep.score,
                     'recommendation': rep.recommendation, 'summary': rep.summary,
                     'strengths': '\n'.join(f"- {x}" for x in rep.strengths),
                     'concerns': '\n'.join(f"- {x}" for x in rep.concerns),
                     'cv_claims': '\n'.join(f"- {x}" for x in rep.cv_claims),
                     'cv_consistency': rep.cv_consistency,
                     'screening': '\n'.join(f"- {x}" for x in rep.screening)})
            s.applicant_id.message_post(body=_("AI interview: %(r)s, %(s)s/100. %(sum)s",
                                               r=dict(s._fields['recommendation'].selection)[rep.recommendation],
                                               s=rep.score, sum=rep.summary))
            self.env.cr.commit()  # one finished report must not be lost to a later failure

    def action_retry_report(self):
        self.filtered(lambda s: s.state in ('done', 'error')).write({'state': 'done'})
        self.env.ref('rag_interview_agent.ir_cron_rag_report')._trigger()

class RagInterviewTurn(models.Model):
    _name = 'rag.interview.turn'
    _description = 'AI Interview Turn'
    _order = 'id'

    interview_id = fields.Many2one('rag.interview', required=True, ondelete='cascade', index=True)
    topic = fields.Char()
    topic_no = fields.Integer("Topic #")
    question = fields.Text(required=True)
    answer = fields.Text()
    score = fields.Integer("Score /10")
    evidence = fields.Text()
    flags = fields.Char()
