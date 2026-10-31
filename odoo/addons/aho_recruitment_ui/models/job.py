from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import html2plaintext

# Rule-based status for the position card. These are explicit rules, not a trained model,
# so the card states each reason in words and HR can check it against the pipeline.
STALE_DEFAULT_DAYS = 7      # used when a stage has no "Days to rot" set
QUIET_DAYS = 14             # no new application for this long
PIPELINE_RATIO = 3          # ongoing applicants needed per remaining hire
DEADLINE_SOON_DAYS = 14


class HrJob(models.Model):
    _inherit = 'hr.job'

    aho_target_date = fields.Date('Hạn tuyển', help='Ngày cần tuyển đủ chỉ tiêu. Dùng để cảnh báo nguy cơ trễ.')
    aho_stage_summary = fields.Json('Tình trạng theo giai đoạn', compute='_compute_aho_status')
    aho_risk = fields.Selection([('ok', 'Chưa có cảnh báo'), ('watch', 'Cần theo dõi'), ('risk', 'Nguy cơ trễ'), ('idle', 'Chưa có dữ liệu')],
                                string='Tình trạng', compute='_compute_aho_status')
    aho_warnings = fields.Json('Cảnh báo', compute='_compute_aho_status')
    aho_hired_label = fields.Char('Tiến độ tuyển', compute='_compute_aho_status')

    def _aho_stages(self, job):
        return self.env['hr.recruitment.stage'].search(
            ['|', ('job_ids', '=', False), ('job_ids', 'in', job.id)], order='sequence, id')

    @api.depends_context('uid', 'company')
    @api.depends('no_of_recruitment', 'aho_target_date', 'description', 'recruiter_id',
                 'application_ids', 'application_ids.active', 'application_ids.stage_id',
                 'application_ids.stage_id.hired_stage', 'application_ids.stage_id.rotting_threshold_days',
                 'application_ids.date_last_stage_update', 'application_ids.create_date',
                 'application_ids.rag_interview_ids.state')
    def _compute_aho_status(self):
        Applicant = self.env['hr.applicant']
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        for job in self:
            if not job.id:
                job.aho_stage_summary, job.aho_warnings, job.aho_risk, job.aho_hired_label = [], [], 'idle', ''
                continue
            applicants = Applicant.search([('job_id', '=', job.id), ('active', '=', True)])
            hired = applicants.filtered(lambda a: a.stage_id.hired_stage)
            ongoing = applicants - hired
            stages = []
            stale_total = 0
            for stage in self._aho_stages(job):
                if stage.hired_stage:
                    continue
                in_stage = ongoing.filtered(lambda a, s=stage: a.stage_id == s)
                limit = stage.rotting_threshold_days or STALE_DEFAULT_DAYS
                stale = in_stage.filtered(lambda a, d=limit: a.date_last_stage_update
                                          and a.date_last_stage_update < now - timedelta(days=d))
                stale_total += len(stale)
                stages.append({'id': stage.id, 'name': stage.name, 'count': len(in_stage),
                               'stale': len(stale), 'stale_days': limit})
            # Core Odoo decrements no_of_recruitment when an applicant is hired.
            # It is the remaining headcount, so do not subtract hired applicants again.
            remaining = max(job.no_of_recruitment or 0, 0)
            done = len(hired)
            target = done + remaining
            warnings, level = [], 'ok'

            def flag(text, lvl):
                nonlocal level
                warnings.append({'text': text, 'level': lvl})
                if lvl == 'risk' or (lvl == 'watch' and level == 'ok'):
                    level = lvl

            if job.aho_target_date and remaining:
                days_left = (job.aho_target_date - today).days
                if days_left < 0:
                    flag(f'Đã quá hạn {-days_left} ngày, còn thiếu {remaining} người', 'risk')
                elif days_left <= DEADLINE_SOON_DAYS:
                    late = [s for s in stages[-2:] if s['count']]
                    if sum(s['count'] for s in late) < remaining:
                        flag(f'Còn {days_left} ngày đến hạn nhưng giai đoạn cuối chưa đủ ứng viên', 'risk')
                    else:
                        flag(f'Còn {days_left} ngày đến hạn', 'watch')
            if remaining and len(ongoing) < remaining * PIPELINE_RATIO:
                flag(f'Pipeline mỏng: {len(ongoing)} ứng viên đang xử lý cho {remaining} chỉ tiêu còn lại', 'watch')
            if stale_total:
                flag(f'{stale_total} ứng viên nằm yên quá lâu ở một giai đoạn', 'watch')
            last_new = max(applicants.mapped('create_date'), default=False)
            if remaining and (not last_new or last_new < now - timedelta(days=QUIET_DAYS)):
                flag(f'Không có hồ sơ mới trong {QUIET_DAYS} ngày', 'watch')
            if 'error' in applicants.mapped('rag_interview_ids.state'):
                flag('Có phiên phỏng vấn tự động bị lỗi', 'watch')
            if not html2plaintext(job.description or '').strip():
                flag('Chưa có JD, phỏng vấn tự động thiếu căn cứ đặt câu hỏi', 'watch')
            if not job.recruiter_id:
                flag('Chưa có HR phụ trách', 'watch')
            if not target and not applicants:
                level = 'idle'

            job.aho_stage_summary = stages
            job.aho_warnings = warnings
            job.aho_risk = level
            job.aho_hired_label = f'{done}/{target}' if target else f'{done}'
