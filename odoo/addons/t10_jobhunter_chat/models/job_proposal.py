"""Private immutable job drafts; only the authenticated BFF can confirm them."""
import hashlib
import html
import json

from psycopg2.errors import UniqueViolation
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .bridge import strict, uuid


class JobProposal(models.Model):
    _name = 'jobhunter.chat.job.proposal'
    _description = 'Recruiter job creation proposal'
    _order = 'id desc'

    owner_id = fields.Many2one('res.users', required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    company_scope = fields.Char(required=True)
    conversation_id = fields.Char(required=True, index=True)
    payload = fields.Json(required=True)
    payload_hash = fields.Char(required=True)
    status = fields.Selection([('pending', 'Pending confirmation'), ('created', 'Created')], default='pending', required=True)
    job_id = fields.Many2one('hr.job', ondelete='set null')
    _same_proposal = models.Constraint(
        'UNIQUE(owner_id, company_scope, conversation_id, payload_hash)',
        'This exact job proposal already exists in this conversation.')

    def create(self, vals_list):
        raise AccessError('Job proposals are server managed.')

    def write(self, values):
        raise AccessError('Job proposals are immutable.')

    def unlink(self):
        raise AccessError('Job proposal receipts must be retained.')

    def _scope(self):
        principal = self.env['jobhunter.chat.bridge']._principal()
        return json.dumps(principal['company_ids'], separators=(',', ':'))

    def _access(self):
        self.ensure_one()
        self.check_access('read')
        if self.owner_id.id != self.env.uid or self.company_scope != self._scope():
            raise AccessError('Job proposal unavailable.')
        bridge = self.env['jobhunter.chat.bridge']
        bridge._record('res.company', self.company_id.id)
        self._department()
        if self.status == 'created':
            if not self.job_id:
                raise AccessError('Created position is unavailable.')
            job = bridge._record('hr.job', self.job_id.id)
            if job.company_id != self.company_id:
                raise AccessError('Created position changed company.')

    def _read_format(self, fnames, load='_classic_read'):
        for proposal in self:
            proposal._access()
        return super()._read_format(fnames=fnames, load=load)

    def _export_rows(self, fields, *, _is_toplevel_call=True):
        for proposal in self:
            proposal._access()
        return super()._export_rows(fields, _is_toplevel_call=_is_toplevel_call)

    @api.model
    def read_group(self, domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None):
        for proposal in self.search(domain):
            proposal._access()
        return super().read_group(domain, groupby=groupby, aggregates=aggregates, having=having, offset=offset, limit=limit, order=order)

    def _department(self):
        if not self.payload['department_id']:
            return
        department = self.env['jobhunter.chat.bridge']._record('hr.department', self.payload['department_id'])
        if department.company_id and department.company_id != self.company_id:
            raise AccessError('Department changed company.')
        if department.name != self.payload['department_name']:
            raise ValidationError('The department changed. Prepare a new proposal.')

    def _prepare(self, conversation_id, arguments):
        scope = self._scope()
        conversation_id = uuid(conversation_id)
        self.env['hr.job'].check_access('create')
        strict(arguments, ('name', 'department_name', 'number_of_recruitment', 'description', 'requirements'))
        for key, maximum in (('name', 300), ('description', 10000), ('requirements', 10000)):
            if not isinstance(arguments[key], str) or len(arguments[key]) > maximum:
                raise ValidationError('Invalid job text.')
        name = arguments['name'].strip()
        count = arguments['number_of_recruitment']
        department_name = arguments['department_name']
        if not name or type(count) is not int or not 1 <= count <= 1000:
            raise ValidationError('Invalid job title or hiring count.')
        if department_name is not None and (not isinstance(department_name, str) or len(department_name) > 200):
            raise ValidationError('Invalid department.')
        company = self.env.company
        department = self.env['hr.department']
        if department_name and department_name.strip():
            department = department.search([
                ('name', '=ilike', department_name.strip()),
                '|', ('company_id', '=', company.id), ('company_id', '=', False)], limit=2)
            department = department.filtered(lambda row: row.name.casefold() == department_name.strip().casefold())
            if len(department) != 1:
                raise ValidationError('Needs input: select one unambiguous existing department.')
        payload = {'name': name, 'number_of_recruitment': count,
                   'department_id': department.id if department else None,
                   'department_name': department.name if department else None,
                   'company_id': company.id, 'description': arguments['description'].strip(),
                   'requirements': arguments['requirements'].strip()}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        domain = [('owner_id', '=', self.env.uid), ('company_scope', '=', scope),
                  ('conversation_id', '=', conversation_id), ('payload_hash', '=', fingerprint)]
        proposal = self.search(domain, limit=1)
        if not proposal:
            try:
                with self.env.cr.savepoint():
                    proposal = super(JobProposal, self).create({
                        'owner_id': self.env.uid, 'company_id': company.id, 'company_scope': scope,
                        'conversation_id': conversation_id, 'payload': payload, 'payload_hash': fingerprint})
            except UniqueViolation:
                proposal = self.search(domain, limit=1)
                if not proposal:
                    raise
        proposal._access()
        return {'ok': True, 'data': {'job_proposal': {
            'id': proposal.id, 'status': 'proposal_ready'}}, 'sources': []}

    def _public(self):
        self._access()
        return {'id': self.id, 'review_hash': self.payload_hash, 'status': self.status,
                'fields': self.payload, 'company_name': self.company_id.name,
                'job_id': self.job_id.id if self.status == 'created' else None}

    def _for_conversation(self, conversation_id):
        domain = [('owner_id', '=', self.env.uid), ('company_scope', '=', self._scope()),
                  ('conversation_id', '=', uuid(conversation_id))]
        result = []
        for proposal in self.search(domain, limit=20):
            try:
                result.append(proposal._public())
            except (AccessError, ValidationError):
                continue
        return result

    def _confirm(self, review_hash):
        self._access()
        if not isinstance(review_hash, str) or review_hash != self.payload_hash:
            raise ValidationError('Review the exact proposal before confirming.')
        # Lock only this access-checked receipt. Creation and receipt update share
        # a savepoint so a typed controller error cannot commit an untracked job.
        with self.env.cr.savepoint():
            self.flush_recordset()
            self.env.cr.execute('SELECT id FROM jobhunter_chat_job_proposal WHERE id=%s FOR UPDATE', [self.id])
            self.invalidate_recordset()
            self._access()
            if self.status == 'created':
                return self._public()
            self.env['hr.job'].check_access('create')
            self._department()
            payload = self.payload
            job = self.env['hr.job'].with_context(default_company_id=self.company_id.id).create({
                'name': payload['name'], 'company_id': self.company_id.id,
                'department_id': payload['department_id'] or False,
                'no_of_recruitment': payload['number_of_recruitment'],
                'description': '<p>' + html.escape(payload['description']).replace('\n', '<br/>') + '</p>',
                'requirements': '<p>' + html.escape(payload['requirements']).replace('\n', '<br/>') + '</p>'})
            super(JobProposal, self).write({'status': 'created', 'job_id': job.id})
            return self._public()
