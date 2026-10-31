"""Private original-user read adapter. Never sudo business records."""
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from odoo import fields, models
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.tools.misc import hash_sign, verify_hash_signed

WORKFLOW_TOOLS = ('workflow_context', 'workflow_save', 'workflow_schedule', 'workflow_prepare', 'workflow_prepare_direct')
WORKFLOW_MODELS = ('jobhunter.chat.case', 'jobhunter.chat.artifact', 'jobhunter.chat.answer')
TOOLS = ('search_jobs', 'get_job', 'search_applicants', 'get_applicant', 'list_documents', 'read_document')
FIELDS = {
    'hr.job': ('id', 'name', 'active', 'description', 'requirements', 'department_id', 'company_id', 'write_date'),
    'hr.applicant': ('id', 'partner_name', 'job_id', 'stage_id', 'active', 'company_id', 'create_date', 'write_date'),
}
DOCUMENT_FIELDS = ('id', 'name', 'mimetype', 'file_size', 'checksum', 'write_date', 'res_model', 'res_id')


class _SourceValidationMemo:
    """One synchronous validation tree, never a transaction or user cache."""
    def __init__(self, scope):
        self.scope = scope
        self.active = True
        self.entries = {}
        self.frames = []
        self.generation = 0

    def clear(self):
        self.entries.clear()
        self.generation += 1

    def include(self, entry):
        if self.frames:
            frame = self.frames[-1]
            frame['artifacts'].update(entry[1])
            frame['depth'] = max(frame['depth'], frame['base'] + entry[2])


def utc(value):
    if not value:
        return ''
    if isinstance(value, str):
        value = fields.Datetime.to_datetime(value)
    return value.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')


def strict(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValidationError('Invalid arguments.')


def positive(value):
    if type(value) is not int or value < 1:
        raise ValidationError('Invalid record ID.')
    return value


def uuid(value):
    if not isinstance(value, str):
        raise ValidationError('Invalid identifier.')
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Invalid identifier.') from None


class ChatBridge(models.AbstractModel):
    _name = 'jobhunter.chat.bridge'
    _description = 'Jobhunter private read bridge'

    def _principal(self):
        user = self.env.user
        if self.env.su or self.env.uid == 1 or not user.active or user.share or not user.has_group('hr_recruitment.group_hr_recruitment_interviewer'):
            raise AccessError('Recruitment access is required.')
        companies = self.env.context.get('allowed_company_ids', [user.company_id.id])
        if not isinstance(companies, (list, tuple)) or not companies or any(type(c) is not int or c < 1 for c in companies):
            raise AccessError('Invalid company selection.')
        if not set(companies) <= set(user.company_ids.ids):
            raise AccessError('Invalid company selection.')
        return {'database_uuid': self._database_uuid(), 'user_id': user.id, 'company_ids': sorted(set(companies))}

    def _database_uuid(self):
        # Config/signing metadata only; never used for a business read.
        return self.env['ir.config_parameter'].sudo().get_str('database.uuid')

    def _contexts(self, refs):
        if not isinstance(refs, list) or len(refs) > 20:
            raise ValidationError('Invalid context.')
        result = set()
        for ref in refs:
            if not isinstance(ref, dict) or not {'model', 'id'} <= set(ref) or set(ref) - {'model', 'id', 'label'}:
                raise ValidationError('Invalid context.')
            if ref['model'] not in (*FIELDS, 'ir.attachment') or ('label' in ref and (not isinstance(ref['label'], str) or len(ref['label']) > 300)):
                raise ValidationError('Invalid context.')
            result.add((ref['model'], positive(ref['id'])))
        return [{'model': model, 'id': rid} for model, rid in sorted(result)]

    def _record(self, model, rid):
        positive(rid)
        record = self.env[model].browse(rid).exists()
        if not record:
            raise AccessError('Source unavailable.')
        record.check_access('read')
        if 'company_id' in record._fields and record.company_id and record.company_id.id not in self._principal()['company_ids']:
            raise AccessError('Source unavailable.')
        return record

    def _field_access(self, model, names):
        for name in names:
            if name not in self.env[model]._fields or not self.env[model].has_field_access(self.env[model]._fields[name], 'read'):
                raise AccessError('Source unavailable.')

    def _attachment(self, rid):
        att = self._record('ir.attachment', rid)
        self._field_access('ir.attachment', (*DOCUMENT_FIELDS, 'type', 'res_field', 'raw'))
        if att.type != 'binary' or att.res_field or att.res_model not in FIELDS or not att.res_id:
            raise AccessError('Source unavailable.')
        self._record(att.res_model, att.res_id)
        return att

    def _anchors(self, refs):
        refs = self._contexts(refs)
        jobs, applicants, attachments, document_parents = set(), set(), set(), set()
        for ref in refs:
            model, rid = ref['model'], ref['id']
            if model == 'hr.job':
                self._record(model, rid)
                jobs.add(rid)
                document_parents.add((model, rid))
            elif model == 'hr.applicant':
                applicant = self._record(model, rid)
                self._field_access(model, ['job_id'])
                applicants.add(rid)
                if applicant.job_id:
                    self._record('hr.job', applicant.job_id.id)
                    jobs.add(applicant.job_id.id)
                document_parents.add((model, rid))
            else:
                att = self._attachment(rid)
                attachments.add(rid)
                if att.res_model == 'hr.job':
                    jobs.add(att.res_id)
                else:
                    applicants.add(att.res_id)
                    applicant = self._record('hr.applicant', att.res_id)
                    self._field_access('hr.applicant', ['job_id'])
                    if applicant.job_id:
                        self._record('hr.job', applicant.job_id.id)
                        jobs.add(applicant.job_id.id)
        return refs, jobs, applicants, attachments, document_parents


    def _allowed_target(self, model, rid, anchors):
        refs, jobs, applicants, attachments, parents = anchors
        if not refs:
            return True
        if model == 'hr.job':
            return rid in jobs
        if rid in applicants:
            return True
        selected_jobs = {ref['id'] for ref in refs if ref['model'] == 'hr.job'}
        if not selected_jobs:
            return False
        self._field_access('hr.applicant', ['job_id'])
        return self._record('hr.applicant', rid).job_id.id in selected_jobs

    def _allowed_document_parent(self, model, rid, anchors):
        refs, jobs, applicants, attachments, parents = anchors
        if not refs or (model, rid) in parents:
            return True
        # A selected job includes its visible applicants' files. Applicant or
        # attachment anchors do not expand to the related job's files.
        if model == 'hr.applicant' and any(ref['model'] == 'hr.job' for ref in refs):
            return self._allowed_target(model, rid, anchors)
        return False

    def _values(self, record, names):
        self._field_access(record._name, names)
        values = {}
        for name in names:
            field = record._fields[name]
            value = record[name]
            if field.type == 'many2one':
                value = value.id or None
            elif field.type == 'datetime':
                value = utc(value)
            elif field.type in ('char', 'text', 'html') and not value:
                value = ''
            elif isinstance(value, str):
                value = value[:300 if name in ('name', 'partner_name') else 4000]
            values[name] = value
        # A complete composite excerpt must fit the shared SourceRef bound, even
        # for control characters whose JSON escaping expands their length.
        while len(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) > 9000:
            name = max((key for key, value in values.items() if isinstance(value, str)), key=lambda key: len(values[key]))
            values[name] = values[name][:len(values[name]) // 2]
        return values

    def _source(self, record, values, index, attachment=None):
        return {'key': f'S{index}', 'model': record._name, 'record_id': record.id,
                'title': str(values.get('name') or values.get('partner_name') or 'Source')[:300],
                'write_date': utc(attachment.write_date if attachment else record.write_date),
                'locator': 'metadata' if attachment else 'record',
                'excerpt': json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                **({'attachment_id': attachment.id, 'checksum': attachment.checksum or ''} if attachment else {})}

    def _execute(self, operation, arguments, context_refs):
        try:
            self._principal()
            anchors = self._anchors(context_refs)
            refs, jobs, applicants, attachments, parents = anchors
            if operation in (*WORKFLOW_TOOLS, 'workflow_confirm'):
                return self._workflow_execute(operation, arguments, refs)
            shapes = {'search_jobs': ('query', 'limit'), 'get_job': ('job_id',),
                      'search_applicants': ('query', 'job_id', 'limit'), 'get_applicant': ('applicant_id',),
                      'list_documents': ('model', 'record_id'), 'read_document': ('attachment_id',)}
            if operation not in shapes:
                raise ValidationError('Unknown operation.')
            strict(arguments, shapes[operation])
            for key in ('job_id', 'applicant_id', 'record_id', 'attachment_id'):
                if key in arguments and not (key == 'job_id' and operation == 'search_applicants' and arguments[key] is None):
                    positive(arguments[key])
            if operation.startswith('search_'):
                if not isinstance(arguments['query'], str) or len(arguments['query']) > 300 or type(arguments['limit']) is not int or not 1 <= arguments['limit'] <= 20:
                    raise ValidationError('Invalid search.')
            sources = []
            if operation in ('get_job', 'get_applicant', 'search_jobs', 'search_applicants'):
                model = 'hr.job' if operation in ('get_job', 'search_jobs') else 'hr.applicant'
                allowed = jobs if model == 'hr.job' else applicants
                if operation.startswith('get_'):
                    rid = arguments['job_id' if model == 'hr.job' else 'applicant_id']
                    if not self._allowed_target(model, rid, anchors):
                        raise AccessError('Source unavailable.')
                    records = self._record(model, rid)
                else:
                    field = 'name' if model == 'hr.job' else 'partner_name'
                    self._field_access(model, [field])
                    domain = [(field, 'ilike', arguments['query'])]
                    if refs:
                        if model == 'hr.applicant':
                            self._field_access(model, ['job_id'])
                            selected_jobs = [ref['id'] for ref in refs if ref['model'] == 'hr.job']
                            domain += ['|', ('id', 'in', list(allowed)), ('job_id', 'in', selected_jobs)]
                        else:
                            domain.append(('id', 'in', list(allowed)))
                    if model == 'hr.applicant' and arguments['job_id'] is not None:
                        self._record('hr.job', arguments['job_id'])
                        domain.append(('job_id', '=', arguments['job_id']))
                    records = self.env[model].search(domain, limit=arguments['limit'], order='id')
                rows = []
                for record in records:
                    self._record(model, record.id)
                    values = self._values(record, FIELDS[model])
                    rows.append(values)
                    sources.append(self._source(record, values, len(sources) + 1))
                noun = 'job' if model == 'hr.job' else 'applicant'
                data = {noun: rows[0]} if operation.startswith('get_') else {noun + 's': rows}
            elif operation == 'list_documents':
                model, rid = arguments['model'], arguments['record_id']
                if model not in FIELDS:
                    raise ValidationError('Invalid source model.')
                parent = self._record(model, rid)
                if not self._allowed_document_parent(model, rid, anchors):
                    # An attachment anchor only permits the selected attachment, not siblings.
                    if not self._allowed_target(model, rid, anchors):
                        raise AccessError('Source unavailable.')
                    domain = [('id', 'in', list(attachments))]
                else:
                    domain = []
                domain += [('res_model', '=', model), ('res_id', '=', rid), ('type', '=', 'binary'), ('res_field', '=', False)]
                documents = []
                for att in self.env['ir.attachment'].search(domain, limit=100, order='id'):
                    self._attachment(att.id)
                    values = self._values(att, DOCUMENT_FIELDS)
                    documents.append(values)
                    sources.append(self._source(parent, values, len(sources) + 1, attachment=att))
                data = {'documents': documents}
            else:
                att = self._attachment(arguments['attachment_id'])
                if refs and att.id not in attachments and not self._allowed_document_parent(att.res_model, att.res_id, anchors):
                    raise AccessError('Source unavailable.')
                if att.file_size > 10 * 1024 * 1024:
                    return {'ok': False, 'data': {}, 'sources': [], 'error': {'code': 'needs_input', 'message': 'Document exceeds 10 MiB.'}}
                content = bytes(att.raw or b'')
                if len(content) > 10 * 1024 * 1024:
                    raise ValidationError('Document exceeds limit.')
                values = self._values(att, ('id', 'name', 'mimetype', 'checksum', 'write_date', 'res_model', 'res_id'))
                values['content_base64'] = base64.b64encode(content).decode('ascii')
                data = {'document': values}
            return {'ok': True, 'data': data, 'sources': sources}
        except (AccessError, MissingError):
            return {'ok': False, 'data': {}, 'sources': [], 'error': {'code': 'access_denied', 'message': 'Source access is unavailable.'}}
        except (ValidationError, TypeError, ValueError, KeyError):
            return {'ok': False, 'data': {}, 'sources': [], 'error': {'code': 'invalid_arguments', 'message': 'Invalid tool arguments.'}}

    def _validate_sources(self, sources, context_refs=None):
        principal = self._principal()
        scope = (self.env.cr, self.env.uid, self.env.su, tuple(principal['company_ids']))
        memo = self.env.context.get('_jobhunter_source_validation_memo')
        if not isinstance(memo, _SourceValidationMemo) or not memo.active or memo.scope != scope:
            memo = _SourceValidationMemo(scope)
            try:
                return self.with_context(_jobhunter_source_validation_memo=memo)._validate_sources(sources, context_refs)
            finally:
                memo.active = False
                memo.clear()
        if not isinstance(sources, list) or len(sources) > 200:
            raise ValidationError('Invalid sources.')
        anchors = self._anchors(context_refs or [])
        states = []
        for source in sources:
            state, key = 'unavailable', ''
            cache_key, frame = None, None
            try:
                if not isinstance(source, dict):
                    raise ValidationError('Invalid source.')
                required = {'key', 'model', 'record_id', 'title', 'write_date', 'locator', 'excerpt'}
                if not required <= set(source):
                    raise ValidationError('Invalid source.')
                for name, bound in (('title', 300), ('write_date', 64), ('locator', 200), ('excerpt', 10000)):
                    if not isinstance(source[name], str) or len(source[name]) > bound:
                        raise ValidationError('Invalid source.')
                if not source['write_date'] or not source['locator']:
                    raise ValidationError('Invalid source.')
                if source.get('checksum') is not None and (not isinstance(source['checksum'], str) or len(source['checksum']) > 128):
                    raise ValidationError('Invalid source.')
                if set(source) - {'key', 'model', 'record_id', 'field', 'attachment_id', 'title', 'write_date', 'checksum', 'locator', 'excerpt'}:
                    raise ValidationError('Invalid source.')
                key = source.get('key', '')
                if not isinstance(key, str) or not re.fullmatch(r'S[1-9][0-9]*', key):
                    raise ValidationError('Invalid source.')
                model = source['model']
                # Key labels are run-local; every other identity/content field
                # and the permitted anchor scope participate in memo identity.
                cache_key = (json.dumps({name: value for name, value in source.items() if name != 'key'}, sort_keys=True, ensure_ascii=False),
                             json.dumps(anchors[0], sort_keys=True))
                cached = memo.entries.get(cache_key)
                if cached is not None:
                    visited = self.env.context.get('_workflow_source_visiting', ())
                    # A shallow cached subtree must not bypass a deeper path's
                    # original cycle or maximum-depth check.
                    state = cached[0] if not set(visited).intersection(cached[1]) and len(visited) + cached[2] <= 40 else 'unavailable'
                    memo.include(cached)
                    states.append({'key': key, 'state': state})
                    continue
                artifact = model == 'jobhunter.chat.artifact'
                frame = {'artifacts': {source['record_id']} if artifact else set(), 'base': int(artifact),
                         'depth': int(artifact), 'generation': memo.generation}
                memo.frames.append(frame)
                if model in WORKFLOW_MODELS:
                    state = self._workflow_source_state(source, anchors)
                    states.append({'key': key, 'state': state})
                    continue
                if model not in FIELDS:
                    raise AccessError('Source unavailable.')
                record = self._record(model, source['record_id'])
                refs, jobs, applicants, attachments, parents = anchors
                if not self._allowed_target(model, record.id, anchors):
                    raise AccessError('Source unavailable.')
                field = source.get('field')
                if field is not None:
                    if field not in FIELDS[model]:
                        raise AccessError('Source unavailable.')
                    self._field_access(model, [field])
                if source.get('attachment_id') is not None:
                    if not re.fullmatch(r'(?:metadata|(?:page|paragraph|line):[1-9][0-9]*(?::chars:[1-9][0-9]*-[1-9][0-9]*)?)', source['locator']):
                        raise ValidationError('Invalid location.')
                    att = self._attachment(source['attachment_id'])
                    if refs and att.id not in attachments and not self._allowed_document_parent(model, record.id, anchors):
                        raise AccessError('Source unavailable.')
                    if (att.res_model, att.res_id) != (model, record.id):
                        raise AccessError('Source unavailable.')
                    state = 'current' if utc(att.write_date) == source['write_date'] and (att.checksum or '') == source.get('checksum') else 'outdated'
                else:
                    if source['locator'] not in (('record',) if field is None else ('record', field, 'field:' + field)):
                        raise ValidationError('Invalid location.')
                    self._field_access(model, FIELDS[model] if field is None else [field])
                    state = 'current' if utc(record.write_date) == source['write_date'] else 'outdated'
            except (AccessError, MissingError, ValidationError, ValueError, TypeError, KeyError):
                pass
            finally:
                if frame is not None:
                    memo.frames.pop()
                    entry = (state, frozenset(frame['artifacts']), frame['depth'])
                    # Unavailable can be path-dependent (cycle/depth); never
                    # poison an independent path. A write also discards work.
                    if state != 'unavailable' and frame['generation'] == memo.generation:
                        memo.entries[cache_key] = entry
                    memo.include(entry)
            states.append({'key': key, 'state': state})
        return states

    def _issue_grant(self, run_id, context_refs, purpose, workflow=None):
        principal = self._principal()
        run_id = uuid(run_id)
        if purpose not in ('execution', 'validation'):
            raise ValidationError('Invalid purpose.')
        refs = self._contexts(context_refs)
        self._anchors(refs)
        now = datetime.now(timezone.utc)
        claims = {'aud': 't10_jobhunter_chat', 'version': 1, 'database_uuid': principal['database_uuid'],
                  'database': self.env.cr.dbname, 'uid': principal['user_id'], 'companies': principal['company_ids'],
                  'run_id': run_id, 'purpose': purpose, 'tools': self._grant_tools(purpose, workflow),
                  'context_refs': refs, 'token_id': str(uuid4()), 'issued_at': int(now.timestamp()), 'expires_at': int((now + timedelta(minutes=5)).timestamp())}
        if workflow is not None:
            if purpose != 'execution' or not isinstance(workflow, dict) or set(workflow) != {'applicant_id', 'approval_id'}:
                raise ValidationError('Invalid workflow capability.')
            self._workflow_anchor(workflow['applicant_id'], refs)
            claims['workflow'] = workflow
        grant = self.env['jobhunter.chat.grant'].sudo().create({
            'token_id': claims['token_id'], 'run_id': run_id, 'user_id': self.env.uid,
            'database_name': self.env.cr.dbname, 'claims': claims,
            'expires_at': (now + timedelta(minutes=5)).replace(tzinfo=None),
        })
        token = hash_sign(self.sudo().env, 't10_jobhunter_chat', claims, expiration=timedelta(minutes=5))
        return grant, token

    def _verify_grant(self, token, database, run_id, operation):
        try:
            if not isinstance(token, str) or len(token) > 20000 or database != self.env.cr.dbname:
                raise ValueError()
            claims = verify_hash_signed(self.sudo().env, 't10_jobhunter_chat', token)
            expected = {'aud', 'version', 'database_uuid', 'database', 'uid', 'companies', 'run_id', 'purpose', 'tools', 'context_refs', 'token_id', 'issued_at', 'expires_at'}
            if not isinstance(claims, dict) or set(claims) not in (expected, expected | {'workflow'}):
                raise ValueError()
            now = datetime.now(timezone.utc).timestamp()
            if claims['aud'] != 't10_jobhunter_chat' or claims['version'] != 1 or claims['database_uuid'] != self._database_uuid() or claims['database'] != database or claims['run_id'] != uuid(run_id):
                raise ValueError()
            if type(claims['uid']) is not int or claims['uid'] <= 1 or claims['purpose'] not in ('execution', 'validation'):
                raise ValueError()
            if type(claims['expires_at']) is not int or type(claims['issued_at']) is not int or not claims['issued_at'] <= now < claims['expires_at'] or not 0 < claims['expires_at'] - claims['issued_at'] <= 300:
                raise ValueError()
            tools = self._grant_tools(claims['purpose'], claims.get('workflow'))
            if claims['tools'] != tools or operation not in tools:
                raise ValueError()
            grant = self.env['jobhunter.chat.grant'].sudo().search([('token_id', '=', claims['token_id']), ('active', '=', True)], limit=1)
            if not grant or grant.claims != claims or grant.expires_at <= fields.Datetime.now():
                raise ValueError()
            return claims
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
            raise AccessError('Invalid or revoked capability.') from None

    def _revoke_grants(self, run_id):
        principal = self._principal()
        grants = self.env['jobhunter.chat.grant'].sudo().search([
            ('run_id', '=', uuid(run_id)), ('user_id', '=', principal['user_id']), ('active', '=', True),
        ])
        grants.filtered(lambda grant: grant.claims.get('companies') == principal['company_ids']).write({'active': False})


    def _grant_tools(self, purpose, workflow=None):
        if purpose != 'execution':
            return ['validate_sources']
        return [*TOOLS, *WORKFLOW_TOOLS, *(['workflow_confirm'] if workflow and workflow.get('approval_id') else []), 'validate_sources']

    def _workflow_anchor(self, applicant_id, refs):
        positive(applicant_id)
        if {'model': 'hr.applicant', 'id': applicant_id} not in self._contexts(refs):
            raise AccessError('Explicitly select this candidate before starting a workflow.')
        return self._record('hr.applicant', applicant_id)

    def _workflow_execute(self, operation, arguments, refs, approval_id=None, scoped_applicant=None):
        shapes = {'workflow_context': ('applicant_id',),
                  'workflow_save': ('case_id', 'expected_version', 'kind', 'content', 'sources'),
                  'workflow_schedule': ('case_id', 'expected_version', 'schedule'),
                  'workflow_prepare': ('case_id', 'expected_version', 'language', 'questions'),
                  'workflow_prepare_direct': ('case_id', 'expected_version', 'language', 'questions'),
                  'workflow_confirm': ('case_id', 'draft_version')}
        strict(arguments, shapes[operation])
        if operation == 'workflow_context':
            applicant_id = positive(arguments['applicant_id'])
            self._workflow_anchor(applicant_id, refs)
            case = self.env['jobhunter.chat.case']._for_applicant(applicant_id)
        else:
            case = self._record('jobhunter.chat.case', positive(arguments['case_id']))
            applicant_id = case.applicant_id.id
            self._workflow_anchor(applicant_id, refs)
            case._access()
        if scoped_applicant is not None and scoped_applicant != applicant_id:
            raise AccessError('Candidate does not match the workflow capability.')
        if operation == 'workflow_context':
            result = case._public()
        elif operation == 'workflow_save':
            result = case._save(arguments['expected_version'], arguments['kind'], arguments['content'], arguments['sources'])
        elif operation == 'workflow_schedule':
            result = case._schedule(arguments['expected_version'], arguments['schedule'])
        elif operation == 'workflow_prepare':
            result = case._prepare(arguments['expected_version'], arguments['language'], arguments['questions'])
        elif operation == 'workflow_prepare_direct':
            result = case._prepare_direct(arguments['expected_version'], arguments['language'], arguments['questions'])
        else:
            if not approval_id:
                raise AccessError('Explicit browser confirmation is required.')
            approval = self._record('jobhunter.chat.approval', positive(approval_id))
            result = case._confirm(arguments['draft_version'], approval)
        return {'ok': True, 'data': {'case': result}, 'sources': case._sources()}

    def _workflow_source_state(self, source, anchors):
        if source.get('field') is not None or source.get('attachment_id') is not None or source['locator'] != 'record':
            raise AccessError('Invalid workflow source.')
        record = self._record(source['model'], positive(source['record_id']))
        case = record if record._name == 'jobhunter.chat.case' else record.case_id
        case._access()
        case._refresh()
        refs = anchors[0]
        if refs:
            self._workflow_anchor(case.applicant_id.id, refs)
        if utc(record.write_date) != source['write_date']:
            return 'outdated'
        if record._name == 'jobhunter.chat.artifact':
            visited = self.env.context.get('_workflow_source_visiting', ())
            if record.id in visited or len(visited) >= 40:
                raise AccessError('Invalid source dependency cycle.')
            state = record.with_context(_workflow_source_visiting=(*visited, record.id))._state()
            expected = record._source(1)['excerpt'] if state == 'current' else None
        elif record._name == 'jobhunter.chat.answer':
            record.session_id._valid()
            state, expected = 'current', record._source(1)['excerpt']
        else:
            state, expected = 'current', self._source(record, {'name': 'Candidate workflow', 'id': case.id, 'applicant_id': case.applicant_id.id, 'job_id': case.job_id.id, 'version': case.version}, 1)['excerpt']
        if expected is not None and source['excerpt'] != expected:
            raise AccessError('Invalid source content.')
        return state
