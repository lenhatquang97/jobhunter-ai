import logging

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from ..models.agent import MAX_ANSWER, AgentError

_logger = logging.getLogger(__name__)

class RagInterviewController(http.Controller):
    """Candidate side. The token in the URL is the only credential."""

    def _session(self, token):
        if not token or len(token) < 32:
            return None
        return request.env['rag.interview'].sudo().search([('token', '=', token)], limit=1) or None

    def _run(self, token, fn):
        s = self._session(token)
        if not s:
            return {'error': 'closed'}
        try:
            return fn(s)
        except AgentError as e:
            _logger.warning("RAG interview %s AI error: %s", s.id, e, exc_info=True)
            return {'error': 'ai', 'detail': str(e)[:240]}
        except UserError as e:
            return {'error': 'busy' if str(e) == 'busy' else 'closed'}
        except Exception as e:  # noqa: BLE001 - public candidate route must return JSON, not 500
            _logger.exception("RAG interview %s unexpected error", s.id)
            return {'error': 'ai', 'detail': str(e)[:240]}

    @http.route('/rag-interview/<string:token>', type='http', auth='public')
    def page(self, token, **kw):
        s = self._session(token)
        return request.render('rag_interview_agent.interview_page', {
            'session': s, 'open': bool(s) and s.state in ('ready', 'running')})

    @http.route('/rag-interview/<string:token>/voice', type='jsonrpc', auth='public')
    def voice(self, token):
        """Ephemeral OpenAI Realtime key + model for the browser's WebRTC call."""
        def fn(s):
            if s.state not in ('ready', 'running'):
                return {'error': 'closed'}
            return {'secret': s._voice_secret(), 'model': s._cfg('realtime_model')}
        return self._run(token, fn)

    @http.route('/rag-interview/<string:token>/start', type='jsonrpc', auth='public')
    def start(self, token):
        return self._run(token, lambda s: s._start())

    @http.route('/rag-interview/<string:token>/answer', type='jsonrpc', auth='public')
    def answer(self, token, text=''):
        text = (text or '').strip()[:MAX_ANSWER]
        if not text:
            return {'error': 'empty'}
        return self._run(token, lambda s: s._answer(text))
