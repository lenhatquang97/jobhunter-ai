from unittest.mock import patch
from uuid import uuid4

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestJobProposal(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'Job Proposal Synthetic Company'})
        cls.other_company = cls.env['res.company'].create({'name': 'Other Proposal Company'})
        cls.user = cls.env['res.users'].create({
            'name': 'Proposal Recruiter', 'login': 'proposal.recruiter',
            'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('hr_recruitment.group_hr_recruitment_manager').id, cls.env.ref('hr.group_hr_user').id])]})
        cls.proposals = cls.env['jobhunter.chat.job.proposal'].with_user(cls.user).with_context(allowed_company_ids=cls.company.ids)
        cls.arguments = {'name': 'Synthetic Python Role', 'department_name': None,
                         'number_of_recruitment': 2, 'description': '<script>untrusted</script>',
                         'requirements': 'Python\nPostgreSQL'}

    def proposal(self, conversation=None, **changes):
        result = self.proposals._prepare(conversation or str(uuid4()), {**self.arguments, **changes})
        return self.proposals.browse(result['data']['job_proposal']['id'])

    def count_jobs(self):
        return self.env['hr.job'].search_count([('company_id', '=', self.company.id)])

    def test_preparation_and_retry_never_create_jobs(self):
        count = self.count_jobs()
        conversation = str(uuid4())
        first = self.proposal(conversation)
        second = self.proposal(conversation)
        self.assertEqual(first, second)
        self.assertEqual(self.count_jobs(), count)
        self.assertEqual(first.status, 'pending')
        bridge = self.env['jobhunter.chat.bridge'].with_user(self.user).with_context(allowed_company_ids=self.company.ids)
        self.assertFalse(bridge._execute('create_job', self.arguments, [])['ok'])
        self.assertEqual(self.count_jobs(), count)

    def test_confirmation_replay_and_new_model_run_return_one_job(self):
        count = self.count_jobs()
        conversation = str(uuid4())
        proposal = self.proposal(conversation)
        first = proposal._confirm(proposal.payload_hash)
        self.env.invalidate_all()
        second = self.proposals.browse(proposal.id)._confirm(first['review_hash'])
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(self.count_jobs(), count + 1)
        same = self.proposal(conversation)
        self.assertEqual(same.id, proposal.id)
        self.assertEqual(same.status, 'created')
        self.assertNotIn('<script>', same.job_id.description)
        self.assertIn('&lt;script&gt;', same.job_id.description)

    def test_changed_fields_require_a_distinct_confirmation(self):
        conversation = str(uuid4())
        first = self.proposal(conversation)
        second = self.proposal(conversation, number_of_recruitment=3)
        self.assertNotEqual(first, second)
        count = self.count_jobs()
        with self.assertRaises(ValidationError):
            second._confirm(first.payload_hash)
        self.assertEqual(self.count_jobs(), count)
        self.assertEqual(second.status, 'pending')

    def test_confirmation_failure_rolls_back_job_and_receipt(self):
        proposal = self.proposal()
        count = self.count_jobs()
        with patch.object(type(proposal), '_public', side_effect=ValidationError('Synthetic response failure')):
            with self.assertRaises(ValidationError):
                proposal._confirm(proposal.payload_hash)
        self.env.invalidate_all()
        self.assertEqual(self.count_jobs(), count)
        self.assertEqual(proposal.status, 'pending')
        self.assertFalse(proposal.job_id)

    def test_private_immutable_receipts_and_company_scope(self):
        proposal = self.proposal()
        other = self.user.copy({'login': 'proposal.other'})
        with self.assertRaises(AccessError):
            proposal.with_user(other)._public()
        with self.assertRaises(AccessError):
            proposal.write({'payload': {}})
        with self.assertRaises(AccessError):
            self.proposals.create({'payload': self.arguments})
        with self.assertRaises(AccessError):
            proposal.unlink()
        self.user.company_ids = [(4, self.other_company.id)]
        with self.assertRaises(AccessError):
            proposal.with_context(allowed_company_ids=[self.company.id, self.other_company.id])._confirm(proposal.payload_hash)

    def test_created_record_access_is_rechecked(self):
        proposal = self.proposal()
        proposal._confirm(proposal.payload_hash)
        proposal.job_id.with_user(self.env.user).write({'company_id': self.other_company.id})
        with self.assertRaises(AccessError):
            proposal._public()
        with self.assertRaises(AccessError):
            proposal.read(['payload'])
        with self.assertRaises(AccessError):
            self.proposals.search_read([('id', '=', proposal.id)], ['payload'])
        with self.assertRaises(AccessError):
            proposal._confirm(proposal.payload_hash)

    def test_department_changes_invalidate_confirmation(self):
        department = self.env['hr.department'].create({'name': 'Proposal Engineering', 'company_id': self.company.id})
        proposal = self.proposal(department_name=department.name)
        department.name = 'Renamed Department'
        count = self.count_jobs()
        with self.assertRaises(ValidationError):
            proposal._confirm(proposal.payload_hash)
        self.assertEqual(self.count_jobs(), count)

    def test_model_grants_can_prepare_but_never_confirm(self):
        bridge = self.env['jobhunter.chat.bridge'].with_user(self.user).with_context(allowed_company_ids=self.company.ids)
        run = str(uuid4())
        grant, token = bridge._issue_grant(run, [], 'execution')
        with self.assertRaises(AccessError):
            bridge._verify_grant(token, self.env.cr.dbname, run, 'create_job')
        grant, token = bridge._issue_grant(run, [], 'execution', job_scope={'conversation_id': str(uuid4())})
        bridge._verify_grant(token, self.env.cr.dbname, run, 'create_job')
        with self.assertRaises(AccessError):
            bridge._verify_grant(token, self.env.cr.dbname, run, 'jobs.confirm')

    def test_native_business_permissions_are_not_elevated(self):
        limited = self.user.copy({'login': 'proposal.limited', 'group_ids': [(6, 0, [self.env.ref('hr_recruitment.group_hr_recruitment_manager').id])]})
        model = self.proposals.with_user(limited)
        result = model._prepare(str(uuid4()), self.arguments)
        proposal = model.browse(result['data']['job_proposal']['id'])
        count = self.count_jobs()
        with self.assertRaises(AccessError):
            proposal._confirm(proposal.payload_hash)
        self.assertEqual(self.count_jobs(), count)
        self.assertEqual(proposal.status, 'pending')

    def test_department_wildcards_cannot_select_a_different_name(self):
        self.env['hr.department'].create({'name': 'Proposal Engineering', 'company_id': self.company.id})
        with self.assertRaises(ValidationError):
            self.proposal(department_name='Proposal Eng%')
