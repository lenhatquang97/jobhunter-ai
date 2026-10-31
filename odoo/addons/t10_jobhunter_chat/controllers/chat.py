"""Session BFF and stateless signed callback. No credential reaches the browser."""
import base64
import json
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from odoo import api, http
from odoo.http import request
from odoo.exceptions import AccessError, MissingError, ValidationError
from ..models.bridge import strict, uuid, positive, WORKFLOW_TOOLS


def error(code, message):
    return {'error': {'code': code, 'message': message}}


def validate_schedule_request(value):
    """Match the strict ScheduleInput transport shape; business checks run later.

    Missing/null fields are intentional needs-input drafts. Do not infer dates,
    zones, email validity or availability while proxying an authenticated request.
    """
    strings = {'start': 64, 'timezone': 100, 'candidate_email': 254}
    integers = {'duration_minutes': (15, 180), 'participant_count': (1, 10)}
    if not isinstance(value, dict) or set(value) - {*strings, *integers, 'participants'}:
        raise ValidationError('Invalid scheduling fields.')
    for key, bound in strings.items():
        if value.get(key) is not None and (not isinstance(value[key], str) or len(value[key]) > bound):
            raise ValidationError('Invalid scheduling text.')
    for key, (low, high) in integers.items():
        if value.get(key) is not None and (type(value[key]) is not int or not low <= value[key] <= high):
            raise ValidationError('Invalid scheduling number.')
    participants = value.get('participants')
    if participants is None:
        return
    if not isinstance(participants, list) or len(participants) > 10:
        raise ValidationError('Invalid participants.')
    required = {'name', 'email', 'timezone', 'availability'}
    for participant in participants:
        if not isinstance(participant, dict) or not required <= set(participant) or set(participant) - (required | {'user_id'}):
            raise ValidationError('Invalid participant fields.')
        for key, bound in (('name', 200), ('email', 254), ('timezone', 100)):
            if not isinstance(participant[key], str) or len(participant[key]) > bound or (key == 'name' and not participant[key]):
                raise ValidationError('Invalid participant text.')
        internal = participant.get('user_id')
        if internal is not None and (type(internal) is not int or internal <= 1):
            raise ValidationError('Invalid participant user.')
        windows = participant['availability']
        if not isinstance(windows, list) or len(windows) > 20:
            raise ValidationError('Invalid availability.')
        for window in windows:
            strict(window, ('start', 'end'))
            if any(not isinstance(window[key], str) or len(window[key]) > 64 for key in ('start', 'end')):
                raise ValidationError('Invalid availability text.')


class JobhunterChat(http.Controller):
    def _scope(self):
        env = request.env
        selected = request.httprequest.cookies.get('cids')
        if selected is None or selected == '':
            companies = [env.user.company_id.id]
        else:
            if not isinstance(selected, str) or not re.fullmatch(r'[1-9][0-9]*(?:-[1-9][0-9]*)*', selected) or len(selected) > 500:
                raise AccessError('Invalid company selection.')
            companies = sorted(set(int(value) for value in selected.split('-')))
        bridge = env['jobhunter.chat.bridge'].with_context(allowed_company_ids=companies)
        principal = bridge._principal()
        # Clean context prevents browser defaults, flags and alternate companies leaking to ORM.
        request.update_env(user=principal['user_id'], context={'allowed_company_ids': principal['company_ids']}, su=False)
        return request.env['jobhunter.chat.bridge'], principal

    def _config(self):
        config = request.env['ir.config_parameter'].sudo()
        base = os.getenv('JOBHUNTER_API_URL') or config.get_str('jobhunter_chat.api_url', '')
        secret = os.getenv('JOBHUNTER_BRIDGE_SECRET') or config.get_str('jobhunter_chat.bridge_secret', '')
        parsed = urlsplit(base)
        if not secret or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValidationError('Chat service is not configured.')
        return base.rstrip('/'), secret

    def _proxy(self, method, path, principal, body=None, capability=None, validation_run=None):
        base, secret = self._config()
        encoded = base64.urlsafe_b64encode(json.dumps(principal, separators=(',', ':')).encode()).decode().rstrip('=')
        headers = {'Authorization': 'Bearer ' + secret, 'X-Jobhunter-Principal': encoded}
        if capability:
            headers.update({'X-Odoo-Capability': capability, 'X-Odoo-Database': request.env.cr.dbname, 'X-Odoo-Validation-Run': validation_run})
        # Pinned server configuration only, no model/user URLs and no redirects/retries.
        try:
            with requests.request(method, base + path, json=body, headers=headers, timeout=(3, 20), allow_redirects=False, stream=True) as response:
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 4 * 1024 * 1024:
                        return error('service_unavailable', 'Chat response exceeded its limit.')
                if not 200 <= response.status_code < 300:
                    codes = {401: 'access_denied', 403: 'access_denied', 404: 'not_found', 409: 'conflict', 422: 'invalid_arguments', 503: 'service_unavailable'}
                    code = codes.get(response.status_code, 'service_unavailable')
                    messages = {'access_denied': 'Chat access is unavailable.', 'not_found': 'The chat item is unavailable.',
                                'conflict': 'Another run is active, or this message was already changed.',
                                'invalid_arguments': 'Check the chat request and try again.', 'service_unavailable': 'Chat service is unavailable.'}
                    return error(code, messages[code])
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError()
                return payload
        except (requests.RequestException, ValueError):
            return error('service_unavailable', 'Chat service is temporarily unavailable.')

    def _grant(self, bridge, run_id, refs, purpose, workflow=None, client_message_id=None):
        # Commit only freshly issued private metadata, before a callback can race the BFF response.
        with bridge.env.registry.cursor() as cr:
            env = api.Environment(cr, bridge.env.uid, {'allowed_company_ids': bridge._principal()['company_ids']}, su=False)
            scope = None
            if workflow is not None:
                scope = {'applicant_id': workflow['applicant_id'], 'approval_id': None}
                if workflow['action'] == 'confirm':
                    case = env['jobhunter.chat.case']._for_applicant(workflow['applicant_id'])
                    approval = case._approval(workflow['data']['draft_version'], client_message_id, run_id)
                    scope['approval_id'] = approval.id
            grant, token = env['jobhunter.chat.bridge']._issue_grant(run_id, refs, purpose, workflow=scope)
            cr.commit()
        return token

    def _revoke(self, bridge, run_id):
        with bridge.env.registry.cursor() as cr:
            env = api.Environment(cr, bridge.env.uid, {'allowed_company_ids': bridge._principal()['company_ids']}, su=False)
            env['jobhunter.chat.bridge']._revoke_grants(run_id)
            cr.commit()

    @http.route('/jobhunter/chat/rpc', type='jsonrpc', auth='user', methods=['POST'], csrf=False)
    def rpc(self, operation=None, arguments=None, csrf_token=None, **kwargs):
        if not isinstance(csrf_token, str) or not request.validate_csrf(csrf_token):
            return error('csrf_failed', 'Refresh the page and try again.')
        try:
            if kwargs or not isinstance(arguments, dict):
                raise ValidationError('Invalid request.')
            bridge, principal = self._scope()
            if operation == 'workflow.get':
                strict(arguments, ('applicant_id',))
                return {'case': bridge.env['jobhunter.chat.case']._for_applicant(positive(arguments['applicant_id']))._public()}
            if operation == 'workflow.interview.create':
                strict(arguments, ('case_id', 'guide_version', 'client_request_id'))
                case = bridge._record('jobhunter.chat.case', positive(arguments['case_id']))
                return case._create_direct(arguments['guide_version'], arguments['client_request_id'])
            if operation in ('workflow.criteria.approve', 'workflow.revoke'):
                strict(arguments, ('case_id', 'version') if operation.endswith('approve') else ('case_id', 'session_id'))
                case = bridge._record('jobhunter.chat.case', positive(arguments['case_id']))
                result = case._approve_criteria(arguments['version']) if operation.endswith('approve') else case._revoke(arguments['session_id'])
                return {'case': result}
            if operation in ('context.search', 'context.documents'):
                if operation == 'context.search':
                    strict(arguments, ('model', 'query'))
                    model = arguments['model']
                    if model not in ('hr.job', 'hr.applicant'):
                        raise ValidationError('Invalid model.')
                    tool = 'search_jobs' if model == 'hr.job' else 'search_applicants'
                    args = {'query': arguments['query'], 'limit': 20}
                    if model == 'hr.applicant':
                        args['job_id'] = None
                    result = bridge._execute(tool, args, [])
                    rows = result['data'].get('jobs' if model == 'hr.job' else 'applicants', [])
                    records = [{'model': model, 'id': row['id'], 'label': row.get('name') or row.get('partner_name') or 'Record'} for row in rows]
                else:
                    strict(arguments, ('model', 'record_id'))
                    result = bridge._execute('list_documents', arguments, [])
                    records = [{'model': 'ir.attachment', 'id': row['id'], 'label': row['name']} for row in result['data'].get('documents', [])]
                return {'records': records} if result['ok'] else {'error': result['error']}
            if operation in ('health', 'conversations.list', 'conversations.create'):
                strict(arguments, ())
                if operation == 'health':
                    return self._proxy('GET', '/health', principal)
                return self._proxy('GET' if operation.endswith('list') else 'POST', '/v1/conversations', principal, {} if operation.endswith('create') else None)
            if operation == 'conversations.get':
                strict(arguments, ('conversation_id',))
                cid = uuid(arguments['conversation_id'])
                scope_id = str(uuid4())
                token = self._grant(bridge, scope_id, [], 'validation')
                return self._proxy('GET', '/v1/conversations/' + cid, principal, capability=token, validation_run=scope_id)
            if operation == 'conversations.archive':
                strict(arguments, ('conversation_id', 'archived'))
                if type(arguments['archived']) is not bool:
                    raise ValidationError('Invalid archive value.')
                return self._proxy('PATCH', '/v1/conversations/' + uuid(arguments['conversation_id']), principal, {'archived': arguments['archived']})
            if operation == 'messages.send':
                required = {'conversation_id', 'client_message_id', 'text', 'context_refs'}
                if not required <= set(arguments) or set(arguments) - (required | {'retry_of_run_id', 'workflow'}):
                    raise ValidationError('Invalid submission.')
                cid = uuid(arguments['conversation_id'])
                client_id = uuid(arguments['client_message_id'])
                text = arguments['text']
                if not isinstance(text, str) or not text.strip() or len(text) > 20000:
                    raise ValidationError('Invalid message.')
                refs = bridge._contexts(arguments['context_refs'])
                bridge._anchors(refs)
                workflow = arguments.get('workflow')
                if workflow is not None:
                    if not isinstance(workflow, dict) or set(workflow) not in ({'action', 'applicant_id'}, {'action', 'applicant_id', 'data'}):
                        raise ValidationError('Invalid workflow request.')
                    workflow = {**workflow, 'data': workflow.get('data', {})}
                    if workflow['action'] not in ('screen', 'schedule', 'report', 'confirm', 'prepare_interview') or not isinstance(workflow['data'], dict):
                        raise ValidationError('Invalid workflow request.')
                    bridge._workflow_anchor(positive(workflow['applicant_id']), refs)
                    if len(json.dumps(workflow['data'])) > 16000:
                        raise ValidationError('Workflow data exceeds its limit.')
                    if workflow['action'] == 'confirm':
                        strict(workflow['data'], ('draft_version',))
                        positive(workflow['data']['draft_version'])
                    elif workflow['action'] == 'schedule':
                        if set(workflow['data']) - {'schedule'}:
                            raise ValidationError('Invalid schedule request fields.')
                        if 'schedule' in workflow['data']:
                            validate_schedule_request(workflow['data']['schedule'])
                    elif workflow['data']:
                        raise ValidationError('Use the chat text to provide workflow input.')
                rid = str(uuid4())
                body = {'run_id': rid, 'client_message_id': client_id, 'text': text, 'context_refs': refs,
                        'capability': self._grant(bridge, rid, refs, 'execution', workflow=workflow, client_message_id=client_id), 'odoo_database': request.env.cr.dbname}
                if workflow is not None:
                    body['workflow'] = workflow
                if 'retry_of_run_id' in arguments:
                    body['retry_of_run_id'] = uuid(arguments['retry_of_run_id']) if arguments['retry_of_run_id'] is not None else None
                return self._proxy('POST', '/v1/conversations/' + cid + '/messages', principal, body)
            if operation == 'runs.events':
                strict(arguments, ('run_id', 'after_seq'))
                rid = uuid(arguments['run_id'])
                seq = arguments['after_seq']
                if type(seq) is not int or not 0 <= seq <= 1000000:
                    raise ValidationError('Invalid cursor.')
                scope_id = str(uuid4())
                token = self._grant(bridge, scope_id, [], 'validation')
                result = self._proxy('GET', f'/v1/runs/{rid}/events?after_seq={seq}', principal, capability=token, validation_run=scope_id)
                if result.get('run', {}).get('status') in ('completed', 'needs_input', 'failed', 'cancelled', 'interrupted'):
                    self._revoke(bridge, rid)
                return result
            if operation == 'runs.cancel':
                strict(arguments, ('run_id',))
                rid = uuid(arguments['run_id'])
                self._revoke(bridge, rid)
                return self._proxy('POST', '/v1/runs/' + rid + '/cancel', principal, {})
            raise ValidationError('Unknown operation.')
        except (AccessError, MissingError):
            return error('access_denied', 'Chat access is unavailable.')
        except (ValidationError, ValueError, TypeError, KeyError):
            return error('invalid_arguments', 'Check the chat request and try again.')

    @http.route('/jobhunter/chat/tool', type='jsonrpc', auth='none', methods=['POST'], csrf=False, save_session=False)
    def tool(self, run_id=None, operation=None, arguments=None, **kwargs):
        try:
            database = request.httprequest.headers.get('X-Odoo-Database', '')
            authorization = request.httprequest.headers.get('Authorization', '')
            if kwargs or not authorization.startswith('Bearer ') or not request.env or database != request.env.cr.dbname:
                raise AccessError('Invalid capability.')
            bridge = request.env['jobhunter.chat.bridge']
            claims = bridge._verify_grant(authorization[7:], database, run_id, operation)
            request.update_env(user=claims['uid'], context={'allowed_company_ids': claims['companies']}, su=False)
            bridge = request.env['jobhunter.chat.bridge']
            principal = bridge._principal()
            if principal['company_ids'] != claims['companies'] or principal['database_uuid'] != claims['database_uuid']:
                raise AccessError('Invalid capability.')
            bridge._anchors(claims['context_refs'])
            if operation == 'validate_sources':
                strict(arguments, ('sources',))
                return {'ok': True, 'data': {'states': bridge._validate_sources(arguments['sources'], claims['context_refs'] if claims['purpose'] == 'execution' else [])}, 'sources': []}
            strict(arguments, ('arguments', 'context_refs'))
            if bridge._contexts(arguments['context_refs']) != claims['context_refs']:
                raise AccessError('Context does not match capability.')
            if operation in (*WORKFLOW_TOOLS, 'workflow_confirm'):
                scope = claims.get('workflow') or {}
                return bridge._workflow_execute(operation, arguments['arguments'], claims['context_refs'],
                                                approval_id=scope.get('approval_id'), scoped_applicant=scope.get('applicant_id'))
            return bridge._execute(operation, arguments['arguments'], claims['context_refs'])
        except (AccessError, MissingError):
            return {'ok': False, 'data': {}, 'sources': [], 'error': {'code': 'access_denied', 'message': 'Source access is unavailable.'}}
        except ValidationError as exc:
            if str(exc).startswith('Needs input:'):
                return {'ok': False, 'data': {}, 'sources': [], 'error': {'code': 'needs_input', 'message': str(exc)}}
            return {'ok': False, 'data': {}, 'sources': [], 'error': {'code': 'invalid_arguments', 'message': str(exc)[:300]}}
        except (ValueError, TypeError, KeyError):
            return {'ok': False, 'data': {}, 'sources': [], 'error': {'code': 'invalid_arguments', 'message': 'Invalid callback arguments.'}}
