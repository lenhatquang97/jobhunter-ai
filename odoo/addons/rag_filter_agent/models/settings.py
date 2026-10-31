from odoo import fields, models

from .screening import DEFAULT_MODEL

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rag_filter_openai_key = fields.Char("OpenAI API Key", config_parameter='rag_filter.openai_key')
    rag_filter_model = fields.Char("Matching Model", config_parameter='rag_filter.model', default=DEFAULT_MODEL)
