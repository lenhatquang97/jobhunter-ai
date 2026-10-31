"""Revocable security metadata. No browser-visible creation or mutation API."""
from datetime import timedelta
from odoo import api, fields, models
from odoo.exceptions import AccessError


class ChatGrant(models.Model):
    _name = 'jobhunter.chat.grant'
    _description = 'Jobhunter private callback grant'
    _order = 'id desc'

    token_id = fields.Char(required=True, index=True)
    run_id = fields.Char(required=True, index=True)
    user_id = fields.Many2one('res.users', required=True, index=True)
    database_name = fields.Char(required=True)
    claims = fields.Json(required=True)
    expires_at = fields.Datetime(required=True)
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError('Chat grants are server managed.')
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su or set(vals) != {'active'} or vals['active'] is not False:
            raise AccessError('Chat grants are immutable; only server revocation is allowed.')
        return super().write(vals)

    def unlink(self):
        raise AccessError('Chat grants cannot be deleted through ORM RPC.')

    @api.autovacuum
    def _gc_expired(self):
        # Private security metadata only. No business records or browser mutation path.
        expired = self.sudo().with_context(active_test=False).search([('expires_at', '<', fields.Datetime.now() - timedelta(days=1))], limit=10000)
        super(ChatGrant, expired).unlink()
