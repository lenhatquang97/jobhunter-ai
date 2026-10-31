from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError


@tagged('post_install', '-at_install')
class TestChatBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'Synthetic Chat Company'})
        cls.other_company = cls.env['res.company'].create({'name': 'Other Synthetic Chat Company'})
        group = cls.env.ref('hr_recruitment.group_hr_recruitment_user')
        cls.user = cls.env['res.users'].create({
            'name': 'Synthetic Chat Recruiter', 'login': 'synthetic.chat.recruiter',
            'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [group.id])],
        })
        cls.job = cls.env['hr.job'].create({'name': 'Synthetic Engineer', 'company_id': cls.company.id})
        cls.applicant = cls.env['hr.applicant'].create({'partner_name': 'Synthetic Applicant', 'job_id': cls.job.id})
        cls.sibling = cls.env['hr.applicant'].create({'partner_name': 'Synthetic Sibling', 'job_id': cls.job.id})
        cls.bridge = cls.env['jobhunter.chat.bridge'].with_user(cls.user).with_context(allowed_company_ids=cls.company.ids)

    def test_scope_and_strict_arguments(self):
        self.assertEqual(self.bridge._principal()['company_ids'], self.company.ids)
        with self.assertRaises(AccessError):
            self.bridge.with_context(allowed_company_ids=self.other_company.ids)._principal()
        with self.assertRaises(AccessError):
            self.env['jobhunter.chat.bridge']._principal()
        result = self.bridge._execute('get_job', {'job_id': self.job.id, 'extra': 1}, [])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'invalid_arguments')

    def test_selected_applicant_anchor_and_source_freshness(self):
        refs = [{'model': 'hr.applicant', 'id': self.applicant.id}]
        allowed = self.bridge._execute('get_job', {'job_id': self.job.id}, refs)
        self.assertTrue(allowed['ok'])
        self.assertTrue(allowed['sources'])
        denied = self.bridge._execute('get_applicant', {'applicant_id': self.sibling.id}, refs)
        self.assertEqual(denied['error']['code'], 'access_denied')
        source = allowed['sources'][0]
        states = self.bridge._validate_sources([source])
        self.assertEqual(states[0]['state'], 'current')
        stale = dict(source, write_date='2000-01-01T00:00:00Z')
        self.assertEqual(self.bridge._validate_sources([stale])[0]['state'], 'outdated')
        forbidden = dict(source, field='salary_expected')
        self.assertEqual(self.bridge._validate_sources([forbidden])[0]['state'], 'unavailable')

    def test_immutable_grants_and_revocation(self):
        grant, token = self.bridge._issue_grant('11111111-1111-4111-8111-111111111111', [], 'execution')
        claims = self.bridge._verify_grant(token, grant.database_name, grant.run_id, 'get_job')
        self.assertEqual(claims['uid'], self.user.id)
        with self.assertRaises(AccessError):
            grant.with_user(self.user).write({'active': False})
        self.bridge._revoke_grants(grant.run_id)
        with self.assertRaises(AccessError):
            self.bridge._verify_grant(token, grant.database_name, grant.run_id, 'get_job')
        validation, token = self.bridge._issue_grant(grant.run_id, [], 'validation')
        with self.assertRaises(AccessError):
            self.bridge._verify_grant(token, validation.database_name, grant.run_id, 'get_job')
        self.assertEqual(self.bridge._verify_grant(token, validation.database_name, grant.run_id, 'validate_sources')['purpose'], 'validation')

    def test_documents_parent_scope_bytes_and_validation(self):
        import base64
        document = self.env['ir.attachment'].create({'name': 'synthetic.txt', 'res_model': 'hr.applicant',
            'res_id': self.applicant.id, 'company_id': self.company.id, 'type': 'binary', 'raw': b'Synthetic verified text'})
        sibling_document = self.env['ir.attachment'].create({'name': 'sibling.txt', 'res_model': 'hr.applicant',
            'res_id': self.applicant.id, 'company_id': self.company.id, 'type': 'binary', 'raw': b'Synthetic sibling text'})
        refs = [{'model': 'ir.attachment', 'id': document.id}]
        result = self.bridge._execute('read_document', {'attachment_id': document.id}, refs)
        self.assertTrue(result['ok'])
        self.assertEqual(base64.b64decode(result['data']['document']['content_base64']), b'Synthetic verified text')
        denied = self.bridge._execute('read_document', {'attachment_id': sibling_document.id}, refs)
        self.assertEqual(denied['error']['code'], 'access_denied')
        listed = self.bridge._execute('list_documents', {'model': 'hr.applicant', 'record_id': self.applicant.id}, refs)
        self.assertEqual([d['id'] for d in listed['data']['documents']], [document.id])
        self.assertEqual(self.bridge._validate_sources(listed['sources'])[0]['state'], 'current')
        forged = dict(listed['sources'][0], record_id=self.sibling.id)
        self.assertEqual(self.bridge._validate_sources([forged])[0]['state'], 'unavailable')
        document.write({'type': 'url', 'url': 'https://example.invalid'})
        denied = self.bridge._execute('read_document', {'attachment_id': document.id}, refs)
        self.assertEqual(denied['error']['code'], 'access_denied')

    def test_controller_csrf_company_and_callback_denials(self):
        from unittest.mock import patch, Mock
        from ..controllers.chat import JobhunterChat
        from ..controllers import chat
        controller = JobhunterChat()
        request = Mock()
        request.env = self.bridge.env
        request.validate_csrf.return_value = False
        with patch.object(chat, 'request', request):
            self.assertEqual(controller.rpc('conversations.list', {}, csrf_token='invalid')['error']['code'], 'csrf_failed')
        request.validate_csrf.return_value = True
        request.httprequest.cookies = {'cids': str(self.other_company.id)}
        with patch.object(chat, 'request', request):
            self.assertEqual(controller.rpc('conversations.list', {}, csrf_token='token')['error']['code'], 'access_denied')
        request.httprequest.headers = {'X-Odoo-Database': self.env.cr.dbname, 'Authorization': 'Bearer invalid'}
        with patch.object(chat, 'request', request):
            self.assertEqual(controller.tool('11111111-1111-4111-8111-111111111111', 'get_job', {})['error']['code'], 'access_denied')

    def test_non_recruiter_and_inactive_membership_denied(self):
        normal = self.env['res.users'].create({'name': 'Synthetic Non Recruiter', 'login': 'synthetic.no.recruitment',
            'company_id': self.company.id, 'company_ids': [(6, 0, self.company.ids)],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.bridge.with_user(normal)._principal()
        portal = self.env['res.users'].create({'name': 'Synthetic Portal', 'login': 'synthetic.portal',
            'company_id': self.company.id, 'company_ids': [(6, 0, self.company.ids)],
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])]})
        with self.assertRaises(AccessError):
            self.bridge.with_user(portal)._principal()
        self.user.write({'active': False})
        self.assertEqual(self.bridge._execute('get_job', {'job_id': self.job.id}, [])['error']['code'], 'access_denied')

    def test_signed_claim_expiry_and_own_read_acl(self):
        from odoo.tools.misc import hash_sign
        grant, token = self.bridge._issue_grant('22222222-2222-4222-8222-222222222222', [], 'execution')
        claims = dict(grant.claims)
        claims.pop('expires_at')
        malformed = hash_sign(self.bridge.sudo().env, 't10_jobhunter_chat', claims)
        with self.assertRaises(AccessError):
            self.bridge._verify_grant(malformed, grant.database_name, grant.run_id, 'get_job')
        claims = dict(grant.claims, expires_at=grant.claims['issued_at'] + 3600)
        malformed = hash_sign(self.bridge.sudo().env, 't10_jobhunter_chat', claims)
        with self.assertRaises(AccessError):
            self.bridge._verify_grant(malformed, grant.database_name, grant.run_id, 'get_job')
        self.assertEqual(self.env['jobhunter.chat.grant'].with_user(self.user).search([('id', '=', grant.id)]).ids, grant.ids)
        with self.assertRaises(AccessError):
            self.env['jobhunter.chat.grant'].with_user(self.user).create({'token_id': 'forged'})
        with self.assertRaises(AccessError):
            grant.with_user(self.user).write({'active': True})
        with self.assertRaises(AccessError):
            grant.with_user(self.user).unlink()

    def test_strict_wrappers_and_no_public_attachment_bypass(self):
        import base64
        hidden = self.env['hr.job'].create({'name': 'Synthetic Hidden Job', 'company_id': self.other_company.id})
        public = self.env['ir.attachment'].create({'name': 'public.txt', 'res_model': 'hr.job', 'res_id': hidden.id,
            'company_id': self.company.id, 'public': True, 'type': 'binary', 'raw': b'Synthetic hidden text'})
        result = self.bridge._execute('read_document', {'attachment_id': public.id}, [])
        self.assertEqual(result['error']['code'], 'access_denied')
        protected = self.env['ir.attachment'].create({'name': 'field.txt', 'res_model': 'hr.applicant', 'res_id': self.applicant.id,
            'company_id': self.company.id, 'res_field': 'email_from', 'type': 'binary', 'raw': b'Synthetic protected text'})
        result = self.bridge._execute('read_document', {'attachment_id': protected.id}, [])
        self.assertEqual(result['error']['code'], 'access_denied')
        for arguments in ({'query': '', 'limit': True}, {'query': '', 'limit': 21}, {'query': '', 'limit': 1, 'domain': []}):
            self.assertEqual(self.bridge._execute('search_jobs', arguments, [])['error']['code'], 'invalid_arguments')
        for refs in ([{'model': 'res.users', 'id': self.user.id}], [{'model': 'hr.job', 'id': True}]):
            self.assertEqual(self.bridge._execute('search_jobs', {'query': '', 'limit': 1}, refs)['error']['code'], 'invalid_arguments')

    def test_callback_original_user_and_exact_context_wrapper(self):
        from unittest.mock import patch, Mock
        from ..controllers.chat import JobhunterChat
        from ..controllers import chat
        refs = [{'model': 'hr.applicant', 'id': self.applicant.id}]
        grant, token = self.bridge._issue_grant('33333333-3333-4333-8333-333333333333', refs, 'execution')
        request = Mock()
        request.env = self.bridge.env
        request.httprequest.headers = {'Authorization': 'Bearer ' + token, 'X-Odoo-Database': self.env.cr.dbname}
        def update_env(user, context, su):
            self.assertFalse(su)
            self.assertEqual(user, self.user.id)
            self.assertEqual(context, {'allowed_company_ids': self.company.ids})
            request.env = self.bridge.with_user(user).with_context(context).env
        request.update_env.side_effect = update_env
        controller = JobhunterChat()
        with patch.object(chat, 'request', request):
            result = controller.tool(grant.run_id, 'get_job', {'arguments': {'job_id': self.job.id}, 'context_refs': refs})
            self.assertTrue(result['ok'])
            self.assertEqual(result['data']['job']['id'], self.job.id)
            result = controller.tool(grant.run_id, 'get_job', {'job_id': self.job.id})
            self.assertEqual(result['error']['code'], 'invalid_arguments')
            result = controller.tool(grant.run_id, 'get_job', {'arguments': {'job_id': self.job.id}, 'context_refs': []})
            self.assertEqual(result['error']['code'], 'access_denied')
            self.user.write({'company_id': self.other_company.id, 'company_ids': [(6, 0, self.other_company.ids)]})
            result = controller.tool(grant.run_id, 'get_job', {'arguments': {'job_id': self.job.id}, 'context_refs': refs})
            self.assertEqual(result['error']['code'], 'access_denied')

    def test_composite_source_is_bounded_exact_json(self):
        import json
        self.job.write({'description': '\x01' * 12000, 'requirements': '\x02' * 12000})
        result = self.bridge._execute('get_job', {'job_id': self.job.id}, [])
        self.assertTrue(result['ok'])
        excerpt = result['sources'][0]['excerpt']
        self.assertLessEqual(len(excerpt), 10000)
        self.assertEqual(json.loads(excerpt), result['data']['job'])

    def test_source_schema_and_execution_anchor_validation(self):
        source = self.bridge._execute('get_job', {'job_id': self.job.id}, [])['sources'][0]
        missing = dict(source)
        missing.pop('excerpt')
        self.assertEqual(self.bridge._validate_sources([missing])[0]['state'], 'unavailable')
        forged = dict(source, locator='https://example.invalid/secret')
        self.assertEqual(self.bridge._validate_sources([forged])[0]['state'], 'unavailable')
        other_job = self.env['hr.job'].create({'name': 'Synthetic Unselected Job', 'company_id': self.company.id})
        refs = [{'model': 'hr.job', 'id': other_job.id}]
        self.assertEqual(self.bridge._validate_sources([source], refs)[0]['state'], 'unavailable')

    def test_selected_job_union_and_related_documents(self):
        refs = [{'model': 'hr.job', 'id': self.job.id}]
        result = self.bridge._execute('get_applicant', {'applicant_id': self.sibling.id}, refs)
        self.assertTrue(result['ok'])
        result = self.bridge._execute('search_applicants', {'query': 'Synthetic', 'job_id': None, 'limit': 20}, refs)
        self.assertEqual({row['id'] for row in result['data']['applicants']}, {self.applicant.id, self.sibling.id})
        document = self.env['ir.attachment'].create({'name': 'related.txt', 'res_model': 'hr.applicant',
            'res_id': self.sibling.id, 'company_id': self.company.id, 'type': 'binary', 'raw': b'Synthetic related text'})
        result = self.bridge._execute('read_document', {'attachment_id': document.id}, refs)
        self.assertTrue(result['ok'])
        result = self.bridge._execute('list_documents', {'model': 'hr.applicant', 'record_id': self.sibling.id}, refs)
        self.assertEqual([row['id'] for row in result['data']['documents']], [document.id])
        self.assertEqual(self.bridge._validate_sources(result['sources'], refs)[0]['state'], 'current')

    def test_expired_grant_cleanup_includes_revoked_metadata(self):
        from datetime import timedelta
        from uuid import uuid4
        from odoo import fields
        grants = self.env['jobhunter.chat.grant'].sudo()
        expired = fields.Datetime.now() - timedelta(days=2)
        recent = fields.Datetime.now() - timedelta(hours=1)
        records = {}
        for name, active, expiration in (
            ('expired_active', True, expired), ('expired_revoked', False, expired),
            ('recent_active', True, recent), ('recent_revoked', False, recent),
        ):
            records[name] = grants.create({
                'token_id': str(uuid4()), 'run_id': str(uuid4()), 'user_id': self.user.id,
                'database_name': self.env.cr.dbname, 'claims': {'fixture': name},
                'expires_at': expiration, 'active': active,
            })
        with self.assertRaises(AccessError):
            records['expired_revoked'].with_user(self.user).unlink()
        grants._gc_expired()
        self.assertFalse(records['expired_active'].exists())
        self.assertFalse(records['expired_revoked'].exists())
        self.assertTrue(records['recent_active'].exists())
        self.assertTrue(records['recent_revoked'].exists())
