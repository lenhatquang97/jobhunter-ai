"""Session-scoped candidate access, with no business browsing or model tools."""
import hashlib
import json
import logging
import re
import secrets
import traceback
from odoo import api, http
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.http import request
from ..models.bridge import strict


class _RedactInterviewPath(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if '/jobhunter/interview/' in message:
            record.msg = re.sub(r'/jobhunter/interview/[A-Za-z0-9_-]{30,100}', '/jobhunter/interview/[redacted]', message)
            record.args = ()
        if record.exc_info:
            detail = ''.join(traceback.format_exception(*record.exc_info))
            record.exc_text = re.sub(r'/jobhunter/interview/[A-Za-z0-9_-]{30,100}', '/jobhunter/interview/[redacted]', detail)
        return True


for _logger in ('werkzeug', 'odoo.http', 'odoo.addons.mail.models.mail_mail', 'odoo.addons.base.models.ir_mail_server'):
    logging.getLogger(_logger).addFilter(_RedactInterviewPath())


class CandidateInterview(http.Controller):
    def _session(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', token):
            raise AccessError('Interview link is unavailable.')
        # Hash lookup is security metadata. Candidate business access is evaluated in
        # the original active recruiter's normal environment on every request.
        metadata = request.env['jobhunter.chat.session'].sudo().search([('token_hash', '=', hashlib.sha256(token.encode()).hexdigest())], limit=1)
        if not metadata:
            raise AccessError('Interview link is unavailable.')
        owner, company, session_id = metadata.owner_id.id, metadata.company_id.id, metadata.id
        env = api.Environment(request.env.cr, owner, {'allowed_company_ids': [company]}, su=False)
        session = env['jobhunter.chat.session'].browse(session_id)
        session._valid()
        return session

    @http.route('/jobhunter/interview/<string:token>', type='http', auth='public', methods=['GET'], sitemap=False)
    def page(self, token, **kwargs):
        try:
            session = self._session(token)
            session._candidate_public()
        except (AccessError, MissingError, ValidationError):
            return request.make_response('This interview link is unavailable or expired.', status=404,
                headers=[('Content-Type', 'text/plain; charset=utf-8'), ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer')])
        nonce = secrets.token_urlsafe(24)
        response = request.render('t10_jobhunter_chat.candidate_interview', {'csrf': request.csrf_token(), 'nonce': nonce, 'json': json})
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    @http.route('/jobhunter/interview/rpc', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def rpc(self, token=None, operation=None, arguments=None, csrf_token=None, **kwargs):
        if not isinstance(csrf_token, str) or not request.validate_csrf(csrf_token):
            return {'error': {'code': 'csrf_failed', 'message': 'Refresh the interview page and try again.'}}
        try:
            if kwargs:
                raise ValidationError('Invalid request.')
            session = self._session(token)
            if operation == 'get':
                strict(arguments, ())
                return {'session': session._candidate_public()}
            if operation == 'answer':
                strict(arguments, ('question_id', 'client_answer_id', 'text'))
                answer = session._answer(**arguments)
                return {'answer': answer, 'session': session._candidate_public()}
            if operation == 'finish':
                strict(arguments, ())
                return {'session': session._finish()}
            raise ValidationError('Invalid action.')
        except (AccessError, MissingError):
            return {'error': {'code': 'access_denied', 'message': 'Interview link is unavailable or expired.'}}
        except (ValidationError, ValueError, TypeError, KeyError):
            return {'error': {'code': 'invalid_arguments', 'message': 'Check the current question and answer; refresh to resume.'}}
