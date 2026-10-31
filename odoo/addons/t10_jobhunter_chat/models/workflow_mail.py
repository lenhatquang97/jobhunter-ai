"""One invitation attempt. SMTP ambiguity is durable and never silently resent."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from .workflow import _INTERNAL


class DeliveryAttempt(models.Model):
    _name = 'jobhunter.chat.delivery'
    _description = 'Private durable SMTP attempt metadata'
    _log_access = False
    mail_record_id = fields.Integer(required=True, index=True)
    status = fields.Selection([('attempted', 'Attempted'), ('accepted', 'Accepted'), ('unknown', 'Unknown')], required=True)
    _one_attempt = models.Constraint('UNIQUE(mail_record_id)', 'An invitation already has a delivery attempt.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError('Delivery metadata is server managed.')
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError('Delivery metadata is server managed.')
        return super().write(vals)

    def unlink(self):
        raise AccessError('Delivery attempts cannot be deleted.')


class WorkflowMail(models.Model):
    _inherit = 'mail.mail'
    jobhunter_workflow_session_id = fields.Many2one('jobhunter.chat.session', index=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        # Resolve the same effective defaults core create will use before making
        # the authorization decision; context/ir.default cannot smuggle a session.
        vals_list = [self._add_missing_default_values(dict(values)) for values in vals_list]
        for values in vals_list:
            if values.get('jobhunter_workflow_session_id'):
                if self.env.context.get('_jobhunter_workflow') is not _INTERNAL:
                    raise AccessError('Invitations require an explicit approved workflow.')
                self.env['jobhunter.chat.session'].browse(values['jobhunter_workflow_session_id'])._valid()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('jobhunter_workflow_session_id') and self.env.context.get('_jobhunter_workflow') is not _INTERNAL:
            raise AccessError('Cannot attach arbitrary mail to an interview session.')
        if self.filtered('jobhunter_workflow_session_id') and not self.env.su and self.env.context.get('_jobhunter_workflow') is not _INTERNAL:
            raise AccessError('Confirmed interview invitations cannot be edited through ORM RPC.')
        return super().write(vals)

    def _workflow_delivery_state(self):
        self.ensure_one()
        # Durable security metadata, no message body or candidate data. Independent
        # transaction survives SMTP acceptance followed by a process/DB failure.
        with self.env.registry.cursor() as cr:
            cr.execute('SELECT status FROM jobhunter_chat_delivery WHERE mail_record_id=%s', [self.id])
            row = cr.fetchone()
            return row[0] if row else None

    def _workflow_claim_delivery(self):
        self.ensure_one()
        with self.env.registry.cursor() as cr:
            cr.execute("INSERT INTO jobhunter_chat_delivery(mail_record_id,status) VALUES (%s,'attempted') ON CONFLICT DO NOTHING RETURNING id", [self.id])
            claimed = bool(cr.fetchone())
            cr.commit()
            return claimed

    def _workflow_finish_delivery(self, state):
        self.ensure_one()
        with self.env.registry.cursor() as cr:
            cr.execute('UPDATE jobhunter_chat_delivery SET status=%s WHERE mail_record_id=%s', [state, self.id])
            cr.commit()

    def send(self, auto_commit=False, raise_exception=False, post_send_callback=None):
        if self.filtered('jobhunter_workflow_session_id') and not self.env.su:
            raise AccessError('Interview delivery is handled only by the server mail queue.')
        return super().send(auto_commit=auto_commit, raise_exception=raise_exception, post_send_callback=post_send_callback)

    def _send(self, auto_commit=False, raise_exception=False, smtp_session=None, alias_domain_id=False, mail_server=False, post_send_callback=None):
        ordinary = self.filtered(lambda mail: not mail.jobhunter_workflow_session_id)
        if ordinary:
            super(WorkflowMail, ordinary)._send(auto_commit=auto_commit, raise_exception=raise_exception, smtp_session=smtp_session,
                alias_domain_id=alias_domain_id, mail_server=mail_server, post_send_callback=post_send_callback)
        for mail in self - ordinary:
            session = mail.jobhunter_workflow_session_id
            owner_env = api.Environment(self.env.cr, session.owner_id.id, {'allowed_company_ids': [session.company_id.id]}, su=False)
            try:
                approved_session = owner_env['jobhunter.chat.session'].browse(session.id)
                approved_session._valid()
                if approved_session.mail_id.id != mail.id:
                    raise AccessError('Only the canonical approved invitation may be sent.')
            except (AccessError, ValidationError):
                mail.write({'state': 'exception', 'failure_reason': 'Interview sources are unavailable; invitation was not sent.'})
                continue
            if self.env['ir.mail_server']._disable_send():
                continue
            if not mail._workflow_claim_delivery():
                # Even a manual core "retry" cannot resend an accepted/ambiguous attempt.
                if mail._workflow_delivery_state() != 'accepted':
                    mail.write({'state': 'exception', 'failure_reason': 'SMTP acceptance is unknown; reconcile manually before any new invitation.'})
                continue
            try:
                result = super(WorkflowMail, mail)._send(auto_commit=auto_commit, raise_exception=raise_exception, smtp_session=smtp_session,
                    alias_domain_id=alias_domain_id, mail_server=mail_server, post_send_callback=post_send_callback)
            except Exception:
                mail._workflow_finish_delivery('unknown')
                raise
            else:
                mail._workflow_finish_delivery('accepted' if mail.state == 'sent' else 'unknown')
        return True
