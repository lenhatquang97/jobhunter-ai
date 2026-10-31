from odoo import api, fields, models


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    # Display-only summaries. Never advance stages, schedule sessions, or call providers.
    aho_interview_state = fields.Selection([
        ('none', 'Chưa tạo link'), ('ready', 'Đã tạo link, chờ ứng viên'),
        ('running', 'Ứng viên đang làm'), ('done', 'Đang tạo báo cáo'),
        ('reported', 'Có báo cáo'), ('error', 'Lỗi xử lý'),
    ], string='Phỏng vấn tự động', compute='_compute_aho_interview_summary',
        help='Ứng viên mở link và trả lời bằng giọng nói. AI hỏi theo JD và CV, '
             'sau đó viết báo cáo tham khảo cho HR. Phiên mới nhất được hiển thị.')
    aho_next_action = fields.Char('Việc tiếp theo', compute='_compute_aho_interview_summary')

    @api.depends('active', 'recruiter_id', 'stage_id', 'stage_id.hired_stage',
                 'rag_interview_ids', 'rag_interview_ids.state')
    def _compute_aho_interview_summary(self):
        actions = {
            'none': 'Xem hồ sơ, mời phỏng vấn tự động nếu phù hợp',
            'ready': 'Chia sẻ link và theo dõi phản hồi của ứng viên',
            'running': 'Chờ ứng viên hoàn tất phỏng vấn',
            'done': 'Chờ AI tạo báo cáo',
            'reported': 'Đọc báo cáo và quyết định giai đoạn tiếp theo',
            'error': 'Kiểm tra lỗi của phiên phỏng vấn',
        }
        for applicant in self:
            session = applicant.rag_interview_ids.sorted('id', reverse=True)[:1]
            state = session.state if session else 'none'
            applicant.aho_interview_state = state
            if not applicant.active:
                applicant.aho_next_action = 'Hồ sơ đã lưu trữ'
            elif applicant.stage_id.hired_stage:
                applicant.aho_next_action = 'Đã tuyển, chuẩn bị nhận việc'
            elif not applicant.recruiter_id:
                applicant.aho_next_action = 'Cần phân công HR phụ trách'
            else:
                applicant.aho_next_action = actions.get(state, 'Xem hồ sơ ứng viên')
