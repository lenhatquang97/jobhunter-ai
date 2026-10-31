from odoo import api, fields, models


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    cv_attachment_ids = fields.Many2many(
        'ir.attachment',
        'hr_applicant_cv_attachment_rel',
        'applicant_id',
        'attachment_id',
        string='CV',
        help='CV files used by the Filter and Interview agents. Upload PDF, DOCX or text files here.',
    )

    aho_next_action = fields.Char('Việc tiếp theo', compute='_compute_aho_next_action')

    @api.depends('active', 'recruiter_id', 'stage_id', 'stage_id.hired_stage')
    def _compute_aho_next_action(self):
        for applicant in self:
            if not applicant.active:
                applicant.aho_next_action = 'Hồ sơ đã lưu trữ'
            elif applicant.stage_id.hired_stage:
                applicant.aho_next_action = 'Đã tuyển, chuẩn bị nhận việc'
            elif not applicant.recruiter_id:
                applicant.aho_next_action = 'Cần phân công HR phụ trách'
            else:
                applicant.aho_next_action = 'Xem hồ sơ và bằng chứng trong Jobhunter Chat'
