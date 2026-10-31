from odoo import fields, models

from .interview import DEFAULTS

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rag_openrouter_key = fields.Char("OpenRouter API Key", config_parameter='rag_interview.openrouter_key')
    rag_openai_key = fields.Char("OpenAI API Key", config_parameter='rag_interview.openai_key')
    rag_turn_model = fields.Char("Live Turn Model", config_parameter='rag_interview.turn_model',
                                 default=DEFAULTS['turn_model'])
    rag_report_model = fields.Char("Report Model", config_parameter='rag_interview.report_model',
                                   default=DEFAULTS['report_model'])
    rag_embed_model = fields.Char("Embedding Model", config_parameter='rag_interview.embed_model',
                                  default=DEFAULTS['embed_model'])
    rag_realtime_model = fields.Char("Realtime Speech Model", config_parameter='rag_interview.realtime_model',
                                     default=DEFAULTS['realtime_model'])
    rag_realtime_voice = fields.Char("Realtime Voice", config_parameter='rag_interview.realtime_voice',
                                     default=DEFAULTS['realtime_voice'])
    rag_transcribe_model = fields.Char("Transcription Model", config_parameter='rag_interview.transcribe_model',
                                       default=DEFAULTS['transcribe_model'])
