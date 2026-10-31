"""Private Odoo-owned workflow state. Mutation entry points are never ORM RPC methods."""
import hashlib
import hmac
import base64
import html
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import html2plaintext
from .bridge import FIELDS, _SourceValidationMemo, positive, strict, utc, uuid
from .workflow_schedule import validate_schedule

_INTERNAL = object()  # An RPC context cannot serialize this identity.
KINDS = ('screening', 'report', 'interview_summary')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def bounded(value, limit=60000):
    if len(json.dumps(value, ensure_ascii=False)) > limit:
        raise ValidationError('Workflow content exceeds its limit.')
    def inspect(item):
        if isinstance(item, dict):
            if any(re.search(r'(?:score|rank|verdict|suitability)', str(key), re.I) for key in item):
                raise ValidationError('Scores, rankings and verdicts are not allowed.')
            for child in item.values():
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
    inspect(value)


class WorkflowProtected(models.AbstractModel):
    _name = 'jobhunter.chat.protected'
    _description = 'Private workflow mutation guard'

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('_jobhunter_workflow') is not _INTERNAL:
            raise AccessError('Workflow records are managed by the approved workflow interface.')
        return super().create(vals_list)

    def write(self, vals):
        if self.env.context.get('_jobhunter_workflow') is not _INTERNAL:
            raise AccessError('Workflow records are immutable through ORM RPC.')
        return super().write(vals)

    def _guard_payload_read(self):
        # Odoo20 search_read bypasses read() and calls _read_format directly.
        # Both formatted payloads and export traversal must validate original sources.
        for record in self:
            case = record if record._name == 'jobhunter.chat.case' else record.case_id
            case._refresh()
            if record._name == 'jobhunter.chat.artifact' and record._state() != 'current':
                raise AccessError('Artifact sources changed or became unavailable.')
            if record._name == 'jobhunter.chat.session':
                record._valid()
            if record._name == 'jobhunter.chat.answer':
                record.session_id._valid()

    def _read_format(self, fnames, load='_classic_read'):
        self._guard_payload_read()
        return super()._read_format(fnames=fnames, load=load)

    @api.model
    def read_group(self, domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None):
        # Group labels/aggregates can themselves disclose question or answer text.
        self.search(domain)._guard_payload_read()
        return super().read_group(domain, groupby=groupby, aggregates=aggregates, having=having, offset=offset, limit=limit, order=order)

    def _export_rows(self, fields, *, _is_toplevel_call=True):
        self._guard_payload_read()
        return super()._export_rows(fields, _is_toplevel_call=_is_toplevel_call)

    def unlink(self):
        raise AccessError('Workflow records cannot be deleted through ORM RPC.')

    def _mutate(self, vals):
        memo = self.env.context.get('_jobhunter_source_validation_memo')
        if isinstance(memo, _SourceValidationMemo):
            memo.clear()
        return self.with_context(_jobhunter_workflow=_INTERNAL).write(vals)

    @api.model
    def _insert(self, vals):
        return self.with_context(_jobhunter_workflow=_INTERNAL).create(vals).with_context(_jobhunter_workflow=None)


class CandidateCase(models.Model):
    _name = 'jobhunter.chat.case'
    _inherit = 'jobhunter.chat.protected'
    _description = 'Recruiter private candidate workflow'
    _order = 'id desc'

    owner_id = fields.Many2one('res.users', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    applicant_id = fields.Many2one('hr.applicant', required=True, index=True)
    job_id = fields.Many2one('hr.job', required=True)
    version = fields.Integer(default=1, required=True)
    criteria_version = fields.Integer(default=1, required=True)
    criteria = fields.Json(default=list)
    criteria_history = fields.Json(default=list)
    criteria_approved = fields.Boolean(default=False)
    source_fingerprint = fields.Char(required=True)
    source_snapshot = fields.Json(default=list)
    draft = fields.Json(default=dict)
    guide = fields.Json(default=dict)
    direct_guide = fields.Json(default=dict)
    direct_guide_version = fields.Integer(default=0, required=True)
    automation_state = fields.Selection([
        ('idle', 'Idle'), ('filtering', 'Filtering'), ('scheduling', 'Scheduling'),
        ('awaiting_candidate', 'Awaiting candidate'), ('interview', 'Interview'),
        ('declined', 'Declined'), ('ghosted', 'Ghosted'), ('reporting', 'Reporting'),
        ('completed', 'Completed'), ('failed', 'Failed'),
    ], default='idle', required=True, index=True)
    automation_error = fields.Char()
    artifact_ids = fields.One2many('jobhunter.chat.artifact', 'case_id')
    session_ids = fields.One2many('jobhunter.chat.session', 'case_id')
    approval_ids = fields.One2many('jobhunter.chat.approval', 'case_id')
    _owner_applicant = models.Constraint('UNIQUE(owner_id, company_id, applicant_id)', 'One private case per recruiter and candidate.')

    def _bridge(self):
        return self.env['jobhunter.chat.bridge']

    def _access(self):
        self.ensure_one()
        principal = self._bridge()._principal()
        self.check_access('read')
        if self.owner_id.id != self.env.uid or self.company_id.id not in principal['company_ids']:
            raise AccessError('Workflow unavailable.')
        applicant = self._bridge()._record('hr.applicant', self.applicant_id.id)
        if applicant.job_id.id != self.job_id.id or applicant.company_id.id != self.company_id.id:
            raise AccessError('Candidate scope changed.')
        self._bridge()._record('hr.job', self.job_id.id)
        self._bridge()._field_access('hr.applicant', ['partner_name', 'email_from', 'job_id', 'company_id'])
        return applicant

    def _lock(self):
        self._access()
        self.env.cr.execute('SELECT id FROM jobhunter_chat_case WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    @api.model
    def _for_applicant(self, applicant_id):
        bridge = self.env['jobhunter.chat.bridge']
        bridge._principal()
        applicant = bridge._record('hr.applicant', positive(applicant_id))
        bridge._field_access('hr.applicant', ['job_id', 'company_id', 'email_from', 'partner_name'])
        if not applicant.job_id or not applicant.company_id:
            raise ValidationError('A candidate needs a readable job and company.')
        bridge._record('hr.job', applicant.job_id.id)
        # Serialize same-user/candidate creation, including concurrent independent chats.
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', [self.env.uid, applicant.id])
        case = self.search([('owner_id', '=', self.env.uid), ('applicant_id', '=', applicant.id), ('company_id', '=', applicant.company_id.id)], limit=1)
        if not case:
            sources, criteria = self._snapshot(applicant)
            case = self._insert({'owner_id': self.env.uid, 'company_id': applicant.company_id.id, 'applicant_id': applicant.id,
                                 'job_id': applicant.job_id.id, 'criteria': criteria, 'source_snapshot': sources,
                                 'source_fingerprint': digest(sources), 'criteria_history': [{'version': 1, 'criteria': criteria, 'sources': sources}]})
        case._refresh()
        return case

    @api.model
    def _snapshot(self, applicant):
        bridge = self.env['jobhunter.chat.bridge']
        job = bridge._record('hr.job', applicant.job_id.id)
        sources = [bridge._source(job, bridge._values(job, FIELDS['hr.job']), 1),
                   bridge._source(applicant, bridge._values(applicant, FIELDS['hr.applicant']), 2)]
        # Candidate email is explicitly field-access checked and versioned without modifying the applicant.
        bridge._field_access('hr.applicant', ['email_from'])
        sources[1]['excerpt'] = json.dumps({**json.loads(sources[1]['excerpt']), 'email_from': applicant.email_from or ''}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        text = html2plaintext(job.requirements or job.description or '').strip()
        lines = [line.strip(' -*\t') for line in re.split(r'[\n\r]+', text) if line.strip(' -*\t')]
        criteria = [{'id': f'C{index + 1}', 'text': line[:2000]} for index, line in enumerate(lines[:20])]
        documents = bridge._execute('list_documents', {'model': 'hr.applicant', 'record_id': applicant.id}, [{'model': 'hr.applicant', 'id': applicant.id}])
        for source in documents.get('sources', []):
            sources.append({**source, 'key': f'S{len(sources) + 1}'})
        return sources, criteria

    def _refresh(self):
        applicant = self._access()
        sources, criteria = self._snapshot(applicant)
        if digest(sources) != self.source_fingerprint:
            self._lock()
            if digest(sources) != self.source_fingerprint:
                version = self.criteria_version + 1
                draft = dict(self.draft or {})
                if draft:
                    draft.update(status='needs_input', guide_ready=False, issues=['Job or candidate source changed; review and approve current criteria.'])
                self._mutate({'source_fingerprint': digest(sources), 'source_snapshot': sources, 'criteria': criteria,
                              'criteria_version': version, 'criteria_approved': False, 'version': self.version + 1,
                              'criteria_history': [*(self.criteria_history or []), {'version': version, 'criteria': criteria, 'sources': sources}],
                              'guide': {}, 'direct_guide': {}, 'draft': draft})

    def _expected(self, version):
        self._lock()
        self._refresh()
        if type(version) is not int or version != self.version:
            raise ValidationError('The workflow changed. Refresh and review the current version.')
        if not self.criteria_approved:
            raise ValidationError('Explicitly approve the current job criteria first.')

    def _approve_criteria(self, version):
        self._lock()
        self._refresh()
        if type(version) is not int or version != self.criteria_version or not self.criteria:
            raise ValidationError('Review the current nonempty job criteria.')
        if not self.criteria_approved:
            self._mutate({'criteria_approved': True, 'version': self.version + 1})
        return self._public()

    def _automation_start(self, expected_version):
        """Treat starting an explicit candidate workflow as approval of the current JD snapshot."""
        self._lock()
        self._refresh()
        if type(expected_version) is not int or expected_version != self.version or not self.criteria:
            raise ValidationError('Refresh the candidate and provide a nonempty JD before starting automation.')
        values = {'automation_state': 'filtering', 'automation_error': False, 'version': self.version + 1}
        if not self.criteria_approved:
            values['criteria_approved'] = True
        self._mutate(values)
        return self._public()

    def _auto_schedule(self, expected_version):
        """Find the earliest free 30-minute slot for explicitly assigned interviewers."""
        self._expected(expected_version)
        applicant = self._access()
        interviewers = applicant.interviewer_ids or self.job_id.interviewer_ids
        if not interviewers:
            raise ValidationError('Assign at least one interviewer to the candidate or job.')
        if not applicant.email_from:
            raise ValidationError('The candidate needs an email address before scheduling.')
        zone_name = self.owner_id.tz or self.company_id.partner_id.tz or 'UTC'
        zone = ZoneInfo(zone_name)
        now_local = fields.Datetime.now().replace(tzinfo=timezone.utc).astimezone(zone)
        duration = 30
        chosen = None
        for day_offset in range(1, 15):
            day = (now_local + timedelta(days=day_offset)).date()
            if day.weekday() >= 5:
                continue
            for hour in range(9, 17):
                for minute in (0, 30):
                    start_local = datetime.combine(day, datetime.min.time()).replace(
                        hour=hour, minute=minute, tzinfo=zone
                    )
                    start_utc = start_local.astimezone(timezone.utc)
                    end_utc = start_utc + timedelta(minutes=duration)
                    if all(self._calendar({
                        'user_id': user.id, 'email': user.email or '',
                    }, start_utc, end_utc) for user in interviewers):
                        chosen = (start_local, end_utc.astimezone(zone))
                        break
                if chosen:
                    break
            if chosen:
                break
        if not chosen:
            raise ValidationError('No shared interviewer availability was found in the next 14 days.')
        start_local, end_local = chosen
        if any(not user.email for user in interviewers):
            raise ValidationError('Every assigned interviewer needs an email address.')
        local_start = start_local.replace(tzinfo=None).isoformat(timespec='minutes')
        local_end = end_local.replace(tzinfo=None).isoformat(timespec='minutes')
        participants = [{
            'name': applicant.partner_name or 'Candidate', 'email': applicant.email_from,
            'timezone': zone_name, 'availability': [{'start': local_start, 'end': local_end}],
            'user_id': None,
        }]
        participants.extend({
            'name': user.name, 'email': user.email, 'timezone': user.tz or zone_name,
            'availability': [], 'user_id': user.id,
        } for user in interviewers)
        schedule = {
            'start': local_start, 'timezone': zone_name, 'duration_minutes': duration,
            'participant_count': len(participants), 'participants': participants,
            'candidate_email': applicant.email_from,
        }
        draft = validate_schedule(schedule, self._calendar)
        if draft['status'] != 'ready':
            raise ValidationError('; '.join(draft['issues'] or draft['missing']))
        draft['version'] = ((self.draft or {}).get('version') or 0) + 1
        self._mutate({
            'draft': draft, 'guide': {}, 'automation_state': 'scheduling',
            'version': self.version + 1,
        })
        return self._public()

    def _prepare_direct(self, expected_version, language, questions):
        self._expected(expected_version)
        if language not in ('en', 'vi') or not isinstance(questions, list) or len(questions) != 5:
            raise ValidationError('Prepare exactly five English or Vietnamese main questions.')
        criteria = {criterion['id'] for criterion in self.criteria}
        normalized = []
        for question in questions:
            if not isinstance(question, dict) or set(question) not in ({'criterion_id', 'question'}, {'criterion_id', 'question', 'follow_up'}):
                raise ValidationError('Invalid question fields.')
            if question['criterion_id'] not in criteria or not isinstance(question['question'], str) or not question['question'].strip() or len(question['question']) > 2000:
                raise ValidationError('Questions must reference current criteria.')
            follow_up = question.get('follow_up')
            if follow_up is not None and (not isinstance(follow_up, str) or not follow_up.strip() or len(follow_up) > 2000):
                raise ValidationError('Provide at most one follow-up per question.')
            normalized.append({**question, 'follow_up': follow_up})
        bounded(normalized)
        version = self.direct_guide_version + 1
        self._mutate({'direct_guide_version': version, 'direct_guide': {
            'version': version, 'language': language, 'questions': normalized,
            'criteria_version': self.criteria_version, 'fingerprint': self.source_fingerprint},
            'version': self.version + 1})
        return self._public()

    def _create_direct(self, guide_version, client_request_id):
        """Browser-only issuance; no URL may pass through a signed model callback."""
        self._lock()
        self._refresh()
        guide_version, client_request_id = positive(guide_version), uuid(client_request_id)
        session = self.session_ids.filtered(lambda item: item.client_request_id == client_request_id)[:1]
        if session:
            if session.guide_version != guide_version:
                raise ValidationError('This interview request already targets a different guide.')
            session._valid()
        else:
            guide = self.direct_guide or {}
            if not self.criteria_approved or guide.get('version') != guide_version or guide.get('criteria_version') != self.criteria_version or guide.get('fingerprint') != self.source_fingerprint:
                raise ValidationError('Review the current approved direct interview guide first.')
            if self.session_ids.filtered(lambda item: item.guide_version == guide_version):
                raise ValidationError('This guide already has an interview. Prepare a new guide before creating another link.')
        # Signing configuration is metadata only. Include database identity and a
        # dedicated purpose to keep this derivation separate from other secrets.
        secret = self.env['ir.config_parameter'].sudo().get_str('database.secret')
        if not secret:
            raise ValidationError('Interview signing configuration is unavailable.')
        payload = json.dumps(['jobhunter.direct-interview.v1', self._bridge()._database_uuid(), self.id, guide_version, client_request_id], separators=(',', ':')).encode()
        token = base64.urlsafe_b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode().rstrip('=')
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if session:
            if not hmac.compare_digest(session.token_hash, token_hash):
                raise AccessError('Interview signing configuration changed. Prepare a new guide.')
        else:
            session = self.env['jobhunter.chat.session']._insert({
                'case_id': self.id, 'owner_id': self.owner_id.id, 'company_id': self.company_id.id,
                'token_hash': token_hash, 'expires_at': fields.Datetime.now() + timedelta(days=7),
                'questions': [{**question, 'id': str(uuid4())} for question in guide['questions']],
                'language': guide['language'], 'fingerprint': self.source_fingerprint, 'dependencies': self.source_snapshot,
                'status': 'awaiting_answers', 'guide_version': guide_version, 'client_request_id': client_request_id,
                # Odoo Integer writes False as zero. Reserve negative values for
                # direct guides, preserving positive booked-draft uniqueness.
                'draft_version': -guide_version})
            self._mutate({'version': self.version + 1})
        return {'case': self._public(), 'session_id': session.id, 'interview_url': '/jobhunter/interview/' + token}

    def _calendar(self, participant, start, end):
        try:
            user = self._bridge()._record('res.users', participant['user_id'])
            if user.share or not user.active or user.company_id.id not in self._bridge()._principal()['company_ids'] or (user.email or '').casefold() != participant['email'].casefold():
                return False
            events = self.env['calendar.event']
            events.check_access('read')
            # Shipped Odoo20 employee read ACL exposes calendar busy metadata.
            # No sudo is used; a custom access revocation returns unverified.
            return not bool(events.search_count(['|', ('user_id', '=', user.id), ('partner_ids', 'in', user.partner_id.ids), ('start', '<', end.replace(tzinfo=None)),
                                                  ('stop', '>', start.replace(tzinfo=None)), ('active', '=', True)]))
        except AccessError:
            return False

    def _schedule(self, expected_version, schedule):
        self._expected(expected_version)
        if self.draft and self.draft.get('booking'):
            raise ValidationError('This case already has a booking. Revoke explicitly; cancellation is a separate manual action.')
        if not isinstance(schedule, dict):
            raise ValidationError('Invalid schedule.')
        merged = {**((self.draft or {}).get('schedule') or {}), **{key: value for key, value in schedule.items() if value is not None}}
        draft = validate_schedule(merged, self._calendar)
        draft['version'] = ((self.draft or {}).get('version') or 0) + 1
        self._mutate({'draft': draft, 'guide': {}, 'version': self.version + 1})
        return self._public()

    def _prepare(self, expected_version, language, questions):
        self._expected(expected_version)
        if self.draft and self.draft.get('booking'):
            raise ValidationError('This draft is already booked; its guide is immutable.')
        if not self.draft or self.draft.get('status') != 'ready' or language not in ('en', 'vi'):
            raise ValidationError('A complete valid scheduling draft is required.')
        if not isinstance(questions, list) or len(questions) != 5:
            raise ValidationError('Prepare exactly five main questions.')
        criteria = {criterion['id'] for criterion in self.criteria}
        for question in questions:
            strict(question, ('criterion_id', 'question', 'follow_up'))
            if question['criterion_id'] not in criteria or not isinstance(question['question'], str) or not question['question'].strip() or len(question['question']) > 2000:
                raise ValidationError('Questions must reference current criteria.')
            if question['follow_up'] is not None and (not isinstance(question['follow_up'], str) or not question['follow_up'].strip() or len(question['follow_up']) > 2000):
                raise ValidationError('Provide at most one follow-up per question.')
        bounded(questions)
        draft = {**self.draft, 'guide_ready': True}
        self._mutate({'draft': draft, 'guide': {'language': language, 'questions': questions, 'draft_version': draft['version'],
                                              'criteria_version': self.criteria_version, 'fingerprint': self.source_fingerprint},
                      'version': self.version + 1})
        return self._public()

    def _save(self, expected_version, kind, content, sources):
        self._expected(expected_version)
        if kind not in KINDS or not isinstance(content, dict):
            raise ValidationError('Invalid artifact.')
        bounded(content)
        states = self._bridge()._validate_sources(sources, [{'model': 'hr.applicant', 'id': self.applicant_id.id}])
        if not sources or any(state['state'] != 'current' for state in states):
            raise ValidationError('Artifacts require current accessible sources.')
        by_key = {source['key']: source for source in sources}
        if len(by_key) != len(sources):
            raise ValidationError('Duplicate source keys.')
        if kind == 'screening':
            strict(content, ('summary', 'findings'))
            if not isinstance(content['summary'], str) or len(content['summary']) > 10000 or not isinstance(content['findings'], list):
                raise ValidationError('Invalid findings.')
            expected = {item['id'] for item in self.criteria}
            actual = []
            for finding in content['findings']:
                strict(finding, ('criterion_id', 'status', 'finding', 'citations', 'question'))
                actual.append(finding['criterion_id'])
                if finding['status'] not in ('supported', 'partial', 'unknown', 'conflicting') or any(not isinstance(finding[key], str) or len(finding[key]) > 10000 for key in ('finding', 'question')) or not isinstance(finding['citations'], list):
                    raise ValidationError('Invalid finding.')
                if finding['status'] != 'unknown' and not finding['citations']:
                    raise ValidationError('Factual findings require exact quotations.')
                for citation in finding['citations']:
                    strict(citation, ('source_key', 'quote'))
                    source = by_key.get(citation['source_key'])
                    if not source or not isinstance(citation['quote'], str) or not citation['quote'] or citation['quote'] not in source['excerpt']:
                        raise ValidationError('Finding quotation does not match its source.')
            if len(actual) != len(expected) or set(actual) != expected:
                raise ValidationError('Include every criterion exactly once.')
        else:
            strict(content, ('text',))
            text = content['text']
            if not isinstance(text, str) or not text.strip() or len(text) > 40000:
                raise ValidationError('Invalid report text.')
            keys = re.findall(r'\[(S[1-9][0-9]*)\]', text)
            if not keys or any(key not in by_key for key in keys):
                raise ValidationError('Reports need verified source citations.')
        number = max(self.artifact_ids.filtered(lambda item: item.kind == kind).mapped('version') or [0]) + 1
        self.env['jobhunter.chat.artifact']._insert({'case_id': self.id, 'owner_id': self.owner_id.id, 'company_id': self.company_id.id,
            'kind': kind, 'content': content, 'sources': sources, 'version': number, 'fingerprint': self.source_fingerprint,
            'criteria_version': self.criteria_version})
        values = {'version': self.version + 1}
        if kind == 'report' and self.automation_state == 'reporting':
            values['automation_state'] = 'completed'
            values['automation_error'] = False
        self._mutate(values)
        return self._public()

    def _booking_for_draft(self, draft_version):
        self.ensure_one()
        committed = self.session_ids.filtered(lambda session: session.draft_version == draft_version)[:1]
        # Compatibility for sessions created before the draft-version column.
        if not committed and self.draft and self.draft.get('version') == draft_version and self.draft.get('booking'):
            committed = self.session_ids.filtered(lambda session: session.id == self.draft['booking'].get('session_id'))[:1]
        return committed

    def _approval(self, draft_version, client_message_id, run_id):
        self._lock()
        self._refresh()
        client_message_id, run_id = uuid(client_message_id), uuid(run_id)
        positive(draft_version)
        found = self.approval_ids.filtered(lambda approval: approval.client_message_id == client_message_id)
        if found and found.draft_version != draft_version:
            raise ValidationError('This confirmation already targets a different draft.')
        committed = self._booking_for_draft(draft_version)
        if committed:
            if found:
                if not found.session_id:
                    found._mutate({'session_id': committed.id})
                return found
            original = self.approval_ids.filtered(lambda item: item.session_id == committed)[:1]
            return self.env['jobhunter.chat.approval']._insert({'case_id': self.id, 'owner_id': self.env.uid, 'company_id': self.company_id.id,
                'client_message_id': client_message_id, 'run_id': run_id, 'draft_version': draft_version,
                'draft_hash': original.draft_hash if original else digest(self.draft), 'session_id': committed.id})
        if found and found.session_id:
            return found
        if not self.criteria_approved or type(draft_version) is not int or not self.draft or self.draft.get('version') != draft_version or self.draft.get('status') != 'ready' or not self.guide or not self.draft.get('guide_ready'):
            raise ValidationError('Review the current complete draft and five-question guide before confirming.')
        found = self.approval_ids.filtered(lambda approval: approval.client_message_id == client_message_id)
        if found:
            if found.draft_version != draft_version or (not found.session_id and found.draft_hash != digest({'draft': self.draft, 'guide': self.guide, 'source': self.source_fingerprint})):
                raise ValidationError('The approval request was already used for a different draft.')
            return found
        return self.env['jobhunter.chat.approval']._insert({'case_id': self.id, 'owner_id': self.env.uid, 'company_id': self.company_id.id,
            'client_message_id': client_message_id, 'run_id': run_id, 'draft_version': draft_version,
            'draft_hash': digest({'draft': self.draft, 'guide': self.guide, 'source': self.source_fingerprint})})

    def _delivery_config(self):
        capture = os.getenv('JOBHUNTER_TEST_CAPTURE') == '1' and self.env.cr.dbname.startswith(('jobhunter_e2e', 'jobhunter_baseline'))
        if capture:
            return os.getenv('JOBHUNTER_PUBLIC_URL', 'http://localhost:8069').rstrip('/'), True
        params = self.env['ir.config_parameter'].sudo()
        base = (os.getenv('JOBHUNTER_PUBLIC_URL') or os.getenv('PUBLIC_URL') or params.get_str('jobhunter_chat.public_url', '')).rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValidationError('Needs input: configure a public HTTPS interview URL and SMTP before sending.')
        # Reading configuration metadata only. Business creation remains original-user.
        if not self.env['ir.mail_server'].sudo().search_count([('active', '=', True)]):
            raise ValidationError('Needs input: configure SMTP before sending.')
        return base, False

    def _confirm(self, draft_version, approval):
        # Controllers return typed failures, so explicitly roll back partial effects
        # when mail/calendar creation fails inside an otherwise successful HTTP request.
        with self.env.cr.savepoint():
            return self._confirm_atomic(draft_version, approval)

    def _automation_dispatch(self, expected_version):
        self._expected(expected_version)
        if self.automation_state != 'scheduling' or not self.draft or self.draft.get('status') != 'ready' or not self.draft.get('guide_ready'):
            raise ValidationError('The automatic schedule and interview guide must be ready before dispatch.')
        with self.env.cr.savepoint():
            return self._confirm_atomic(self.draft['version'], None, automatic=True)

    def _confirm_atomic(self, draft_version, approval, automatic=False):
        self._lock()
        self._refresh()
        if not automatic:
            if not approval or approval._name != 'jobhunter.chat.approval' or approval.case_id != self or approval.owner_id.id != self.env.uid:
                raise AccessError('An explicit browser confirmation capability is required.')
            approval.check_access('read')
            if type(draft_version) is not int or draft_version != approval.draft_version:
                raise ValidationError('Confirmation does not match the approved draft version.')
        committed = self._booking_for_draft(draft_version)
        if committed:
            if approval and not approval.session_id:
                approval._mutate({'session_id': committed.id})
            return self._public()
        approval_valid = automatic or (
            approval.draft_version == draft_version
            and approval.draft_hash == digest({'draft': self.draft, 'guide': self.guide, 'source': self.source_fingerprint})
        )
        if not self.criteria_approved or not self.draft or self.draft.get('version') != draft_version or not self.guide or not approval_valid:
            raise ValidationError('The approved draft changed. Review and confirm again.')
        checked = validate_schedule(self.draft['schedule'], self._calendar)
        if checked['status'] != 'ready':
            raise ValidationError('Availability changed. Review the scheduling draft again.')
        # Serialize reservations by named internal calendar before the second conflict check.
        for user_id in sorted({p['user_id'] for p in self.draft['schedule']['participants'] if p['user_id']}):
            self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', [671029, user_id])
        if validate_schedule(self.draft['schedule'], self._calendar)['status'] != 'ready':
            raise ValidationError('The calendar slot is no longer available.')
        base, capture = self._delivery_config()
        token = secrets.token_urlsafe(32)
        questions = [{**question, 'id': str(uuid4())} for question in self.guide['questions']]
        session = self.env['jobhunter.chat.session']._insert({'case_id': self.id, 'owner_id': self.owner_id.id, 'company_id': self.company_id.id,
            'token_hash': hashlib.sha256(token.encode()).hexdigest(), 'expires_at': fields.Datetime.now() + timedelta(days=7),
            'questions': questions, 'language': self.guide['language'], 'fingerprint': self.source_fingerprint,
            'dependencies': self.source_snapshot, 'status': 'prepared' if automatic else 'awaiting_answers', 'draft_version': draft_version})
        # Suppress core calendar invitations; exactly one candidate invitation is queued below.
        event = self.env['calendar.event'].with_context(dont_notify=True, no_mail_to_attendees=True, mail_create_nosubscribe=True,
            mail_create_nolog=True, mail_notrack=True).create({'name': 'Interview: ' + self.applicant_id.partner_name,
                'start': datetime.fromisoformat(checked['starts_at_utc'].replace('Z', '')), 'stop': datetime.fromisoformat(checked['ends_at_utc'].replace('Z', '')),
                'user_id': self.env.uid, 'partner_ids': [(6, 0, self.env['res.users'].browse([p['user_id'] for p in self.draft['schedule']['participants'] if p['user_id']]).mapped('partner_id').ids)], 'description': 'Jobhunter interview reservation awaiting candidate response.' if automatic else 'Confirmed Jobhunter typed interview reservation.'})
        link = base + '/jobhunter/interview/' + token
        mail = self.env['mail.mail'].with_context(mail_create_nosubscribe=True, _jobhunter_workflow=_INTERNAL).create({
            'jobhunter_workflow_session_id': session.id,
            'subject': checked['email_preview']['subject'], 'email_from': self.env.user.email_formatted or self.env.company.email or 'jobhunter@example.invalid',
            'email_to': checked['email_preview']['to'], 'body_html': '<p>' + html.escape(checked['email_preview']['body']) + '</p><p><a href="' + html.escape(link, quote=True) + '">Open your private interview</a></p>',
            'auto_delete': False})
        session._mutate({'event_id': event.id, 'mail_id': mail.id})
        self.env['jobhunter.chat.artifact']._insert({'case_id': self.id, 'owner_id': self.owner_id.id, 'company_id': self.company_id.id,
            'kind': 'booking', 'content': {'text': f'Odoo reservation event {event.id} and typed interview session {session.id} were created; one candidate invitation is queued. This does not establish inbox delivery or candidate participation.'},
            'sources': self.source_snapshot, 'version': 1, 'fingerprint': self.source_fingerprint, 'criteria_version': self.criteria_version})
        if approval:
            approval._mutate({'session_id': session.id})
        draft = {**self.draft, 'booking': {'event_id': event.id, 'session_id': session.id, 'mail_status': 'queued'}}
        self._mutate({
            'draft': draft,
            'automation_state': 'awaiting_candidate' if automatic else 'interview',
            'version': self.version + 1,
        })
        return self._public()

    def _revoke(self, session_id):
        self._lock()
        session = self.session_ids.filtered(lambda item: item.id == positive(session_id))
        if not session:
            raise AccessError('Session unavailable.')
        if session.status != 'revoked':
            session._mutate({'status': 'revoked'})
            self._mutate({'version': self.version + 1})
        return self._public()

    def _public(self):
        self._refresh()
        applicant = self._access()
        result = {'id': self.id, 'applicant_id': applicant.id, 'job_id': self.job_id.id, 'version': self.version,
                  'applicant_name': applicant.partner_name or '', 'candidate_email': applicant.email_from or '',
                  'criteria_version': self.criteria_version, 'criteria_approved': self.criteria_approved, 'criteria': self.criteria or [],
                  'documents': [], 'screening': None, 'schedule': self.draft or None, 'interview': None, 'direct_interview': None, 'report': None, 'interview_summary': None,
                  'direct_guide': {key: self.direct_guide[key] for key in ('version', 'language', 'questions')} if self.direct_guide else None,
                  'automation_state': self.automation_state, 'automation_error': self.automation_error or None}
        docs = self._bridge()._execute('list_documents', {'model': 'hr.applicant', 'record_id': applicant.id}, [{'model': 'hr.applicant', 'id': applicant.id}])
        result['documents'] = [{'id': row['id'], 'name': row['name']} for row in docs.get('data', {}).get('documents', [])]
        for kind in KINDS:
            artifact = self.artifact_ids.filtered(lambda item: item.kind == kind).sorted('version', reverse=True)[:1]
            if artifact:
                result[kind] = artifact._public()
        session = self.session_ids.sorted('id', reverse=True)[:1]
        if session:
            result['interview'] = session._summary()
            if result['schedule'] and result['schedule'].get('booking'):
                booked = self.session_ids.filtered(lambda item: item.id == result['schedule']['booking']['session_id'])[:1]
                result['schedule'] = {**result['schedule'], 'booking': {**result['schedule']['booking'], 'mail_status': booked._mail_status() if booked else 'unknown'}}
        if self.direct_guide:
            direct = self.session_ids.filtered(lambda item: item.guide_version == self.direct_guide['version'])[:1]
            if direct:
                result['direct_interview'] = direct._summary()
        return result

    def _sources(self):
        self._refresh()
        sources = list(self.source_snapshot or [])
        for kind in (*KINDS, 'booking'):
            artifact = self.artifact_ids.filtered(lambda item: item.kind == kind).sorted('version', reverse=True)[:1]
            if artifact and artifact._state() == 'current':
                sources.append(artifact._source(len(sources) + 1))
        for session in self.session_ids:
            # The case's original-user access was checked above. A historical
            # session can expire or lose dependencies without blocking fresh
            # case workflows. Catch only its expected availability denial;
            # answer-source errors and other failures must still propagate.
            try:
                session._valid()
            except AccessError:
                continue
            for answer in session.answer_ids:
                sources.append(answer._source(len(sources) + 1))
        return sources[:200]


class Artifact(models.Model):
    _name = 'jobhunter.chat.artifact'
    _inherit = 'jobhunter.chat.protected'
    _description = 'Immutable candidate artifact revision'
    case_id = fields.Many2one('jobhunter.chat.case', required=True, index=True)
    owner_id = fields.Many2one('res.users', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    kind = fields.Selection([(kind, kind) for kind in (*KINDS, 'booking')], required=True)
    content = fields.Json(required=True)
    sources = fields.Json(required=True)
    version = fields.Integer(required=True)
    criteria_version = fields.Integer(required=True)
    fingerprint = fields.Char(required=True)

    def write(self, vals):
        raise AccessError('Artifact revisions are immutable.')

    def _state(self):
        self.case_id._access()
        states = self.env['jobhunter.chat.bridge']._validate_sources(self.sources)
        if any(state['state'] == 'unavailable' for state in states):
            return 'unavailable'
        return 'current' if self.fingerprint == self.case_id.source_fingerprint and all(state['state'] == 'current' for state in states) else 'outdated'

    def _public(self):
        state = self._state()
        return {'id': self.id, 'kind': self.kind, 'content': self.content if state == 'current' else None,
                'sources': self.sources if state == 'current' else [], 'source_state': state, 'version': self.version}

    def _source(self, index):
        self.case_id._access()
        source = self.env['jobhunter.chat.bridge']._source(self, {'name': self.kind, 'content': self.content}, index)
        source['excerpt'] = source['excerpt'][:10000]
        return source


class Approval(models.Model):
    _name = 'jobhunter.chat.approval'
    _inherit = 'jobhunter.chat.protected'
    _description = 'Explicit exact-draft browser approval'
    case_id = fields.Many2one('jobhunter.chat.case', required=True, index=True)
    owner_id = fields.Many2one('res.users', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    client_message_id = fields.Char(required=True)
    run_id = fields.Char(required=True)
    draft_version = fields.Integer(required=True)
    draft_hash = fields.Char(required=True)
    session_id = fields.Many2one('jobhunter.chat.session')
    _approval_idempotency = models.Constraint('UNIQUE(case_id, client_message_id)', 'This confirmation is already recorded.')


class InterviewSession(models.Model):
    _name = 'jobhunter.chat.session'
    _inherit = 'jobhunter.chat.protected'
    _description = 'Private expiring typed interview session'
    case_id = fields.Many2one('jobhunter.chat.case', required=True, index=True)
    owner_id = fields.Many2one('res.users', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    token_hash = fields.Char(required=True, index=True, )
    draft_version = fields.Integer(required=True, default=0)
    guide_version = fields.Integer(default=0)
    client_request_id = fields.Char(index=True)
    _direct_request_once = models.Constraint('UNIQUE(case_id, client_request_id)', 'This interview request is already recorded.')
    _one_case_draft_booking = models.Constraint('UNIQUE(case_id, draft_version)', 'This case draft already has an interview booking.')
    expires_at = fields.Datetime(required=True)
    questions = fields.Json(required=True)
    language = fields.Selection([('en', 'English'), ('vi', 'Vietnamese')], required=True)
    fingerprint = fields.Char(required=True)
    dependencies = fields.Json(required=True)
    status = fields.Selection([(state, state) for state in ('prepared', 'awaiting_answers', 'in_progress', 'completed', 'partial', 'declined', 'ghosted', 'revoked', 'expired')], required=True)
    event_id = fields.Many2one('calendar.event')
    mail_id = fields.Many2one('mail.mail')
    answer_ids = fields.One2many('jobhunter.chat.answer', 'session_id')

    def _valid(self):
        self.ensure_one()
        self.case_id._refresh()
        if self.status in ('declined', 'ghosted', 'revoked', 'expired') or self.expires_at <= fields.Datetime.now() or self.fingerprint != self.case_id.source_fingerprint:
            raise AccessError('Interview link is unavailable or expired.')
        states = self.env['jobhunter.chat.bridge']._validate_sources(self.dependencies)
        if any(state['state'] != 'current' for state in states):
            raise AccessError('Interview sources changed or became unavailable.')

    def _mail_status(self):
        if not self.mail_id:
            return 'not_sent'
        # Recruiter created this protected mail; avoid exposing body/link through summaries.
        delivery = self.mail_id._workflow_delivery_state()
        if delivery in ('attempted', 'unknown'):
            return 'unknown'
        if self.mail_id.failure_reason == 'Interview sources are unavailable; invitation was not sent.':
            return 'not_sent'
        state = self.mail_id.state
        return {'outgoing': 'queued', 'sent': 'accepted', 'exception': 'failed', 'cancel': 'cancelled'}.get(state, 'unknown')

    def _summary(self):
        self.case_id._access()
        status = self.status
        if status != 'revoked' and self.expires_at <= fields.Datetime.now():
            status = 'expired'
        available = self.fingerprint == self.case_id.source_fingerprint and status not in ('revoked', 'expired', 'ghosted')
        answers = [{'id': answer.id, 'question': answer.question, 'answer': answer.text} for answer in self.answer_ids.sorted('id')] if available else []
        return {'id': self.id, 'guide_version': self.guide_version, 'status': status, 'submitted_count': len(self.answer_ids), 'question_count': len(self.questions),
                'answers': answers, 'mail_status': self._mail_status(), **({} if available else {'source_state': 'unavailable'})}

    def _candidate_public(self):
        if self.status != 'declined':
            self._valid()
        answered = {answer.question_id for answer in self.answer_ids}
        current = next((question for question in self.questions if question['id'] not in answered), None)
        return {'status': self.status, 'language': self.language, 'submitted_count': len(answered), 'question_count': len(self.questions),
                'response_required': self.status == 'prepared',
                'current_question': current if self.status in ('awaiting_answers', 'in_progress') else None,
                'answers': [{'question_id': answer.question_id, 'question': answer.question, 'answer': answer.text} for answer in self.answer_ids.sorted('id')]}

    def _respond(self, response):
        self.case_id._lock()
        if response not in ('accepted', 'declined'):
            raise ValidationError('Choose accepted or declined.')
        if self.status == 'prepared':
            self._valid()
            if response == 'accepted':
                self._mutate({'status': 'awaiting_answers'})
                self.case_id._mutate({'automation_state': 'interview', 'version': self.case_id.version + 1})
            else:
                self._mutate({'status': 'declined'})
                if self.event_id:
                    self.event_id.with_context(dont_notify=True).write({'active': False})
                self.case_id._mutate({'automation_state': 'declined', 'version': self.case_id.version + 1})
        elif (response == 'accepted' and self.status not in ('awaiting_answers', 'in_progress', 'completed', 'partial')) or (response == 'declined' and self.status != 'declined'):
            raise ValidationError('This invitation already has a different response.')
        return self._candidate_public()

    def _answer(self, question_id, client_answer_id, text):
        self.case_id._lock()
        self._valid()
        client_answer_id, question_id = uuid(client_answer_id), uuid(question_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValidationError('Answer must contain 1 to 10000 characters.')
        found = self.answer_ids.filtered(lambda answer: answer.client_answer_id == client_answer_id)
        if found:
            if found.question_id != question_id or found.text != text:
                raise ValidationError('This answer identifier already contains a different answer.')
            return {'answer_id': found.id, 'question_id': found.question_id, 'text': found.text}
        current = self._candidate_public()['current_question']
        if not current or current['id'] != question_id or self.status not in ('awaiting_answers', 'in_progress'):
            raise ValidationError('Submit only the current unanswered question.')
        answer = self.env['jobhunter.chat.answer']._insert({'case_id': self.case_id.id, 'session_id': self.id, 'owner_id': self.owner_id.id,
            'company_id': self.company_id.id, 'question_id': question_id, 'client_answer_id': client_answer_id,
            'question': current['question'], 'text': text})
        self._mutate({'status': 'in_progress'})
        self.case_id._mutate({'version': self.case_id.version + 1})
        return {'answer_id': answer.id, 'question_id': question_id, 'text': text}

    def _finish(self):
        self.case_id._lock()
        self._valid()
        if self.status not in ('completed', 'partial'):
            self._mutate({'status': 'completed' if len(self.answer_ids) == len(self.questions) else 'partial'})
            self.case_id._mutate({'automation_state': 'reporting', 'version': self.case_id.version + 1})
        return self._candidate_public()

    @api.model
    def _expire_unanswered_invitations(self):
        sessions = self.search([('status', '=', 'prepared'), ('expires_at', '<=', fields.Datetime.now())])
        for session in sessions:
            session._mutate({'status': 'ghosted'})
            if session.event_id:
                session.event_id.with_context(dont_notify=True).write({'active': False})
            session.case_id._mutate({'automation_state': 'ghosted', 'version': session.case_id.version + 1})
        return len(sessions)


class Answer(models.Model):
    _name = 'jobhunter.chat.answer'
    _inherit = 'jobhunter.chat.protected'
    _description = 'Immutable explicitly submitted candidate answer'
    case_id = fields.Many2one('jobhunter.chat.case', required=True, index=True)
    session_id = fields.Many2one('jobhunter.chat.session', required=True, index=True)
    owner_id = fields.Many2one('res.users', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    question_id = fields.Char(string='Question identifier', required=True)
    client_answer_id = fields.Char(required=True)
    question = fields.Text(required=True)
    text = fields.Text(required=True)
    _answer_idempotency = models.Constraint('UNIQUE(session_id, client_answer_id)', 'Answer identifier already submitted.')
    _answer_once = models.Constraint('UNIQUE(session_id, question_id)', 'Question already answered.')

    def write(self, vals):
        raise AccessError('Submitted answers are immutable.')

    def _source(self, index):
        self.case_id._access()
        self.session_id._valid()
        source = self.env['jobhunter.chat.bridge']._source(self, {'name': self.question[:300], 'question': self.question, 'answer': self.text}, index)
        source['excerpt'] = source['excerpt'][:10000]
        return source
