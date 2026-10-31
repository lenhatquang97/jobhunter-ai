from datetime import timedelta
from uuid import uuid4
from odoo import fields
from odoo.tests.common import BaseCase, TransactionCase, get_db_name, tagged
from odoo.exceptions import AccessError, ValidationError


@tagged('post_install', '-at_install')
class TestCandidateWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'Workflow Synthetic Company'})
        cls.user = cls.env['res.users'].create({'name': 'Workflow Recruiter', 'login': 'workflow.recruiter',
            'email': 'workflow.recruiter@example.invalid', 'tz': 'Asia/Ho_Chi_Minh',
            'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('hr_recruitment.group_hr_recruitment_user').id])]})
        cls.job = cls.env['hr.job'].create({'name': 'Workflow Engineer', 'company_id': cls.company.id,
                                          'requirements': '<p>Python experience</p><p>Clear communication</p>'})
        cls.applicant = cls.env['hr.applicant'].create({'partner_name': 'Workflow Candidate', 'job_id': cls.job.id,
                                                      'email_from': 'netlink269@gmail.com'})
        cls.bridge = cls.env['jobhunter.chat.bridge'].with_user(cls.user).with_context(allowed_company_ids=cls.company.ids)

    def case(self):
        return self.bridge.env['jobhunter.chat.case']._for_applicant(self.applicant.id)

    def ready(self):
        case = self.case()
        case._approve_criteria(case.criteria_version)
        start = (fields.Datetime.now() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
        schedule = {'start': start.isoformat(), 'duration_minutes': 30, 'participant_count': 2,
                    'timezone': 'Asia/Ho_Chi_Minh', 'candidate_email': 'netlink269@gmail.com',
                    'participants': [{'name': 'Candidate', 'email': 'netlink269@gmail.com', 'timezone': 'Asia/Ho_Chi_Minh', 'user_id': None,
                                      'availability': [{'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat()}]},
                                     {'name': 'Recruiter', 'email': 'recruiter@example.invalid', 'timezone': 'Asia/Ho_Chi_Minh', 'user_id': None,
                                      'availability': [{'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat()}]}]}
        case._schedule(case.version, schedule)
        case._prepare(case.version, 'en', [{'criterion_id': case.criteria[0]['id'], 'question': f'Question {i}?', 'follow_up': None} for i in range(5)])
        return case

    def test_private_case_criteria_and_stale_versions(self):
        case = self.case()
        self.assertEqual(case.id, self.case().id)
        self.assertFalse(case._public()['criteria_approved'])
        self.assertIn('Python experience', str(case.criteria))
        with self.assertRaises(AccessError):
            case.write({'criteria_approved': True})
        with self.assertRaises(AccessError):
            case.env['jobhunter.chat.case'].create({'applicant_id': self.applicant.id})
        case._approve_criteria(case.criteria_version)
        self.assertTrue(case._public()['criteria_approved'])
        with self.assertRaises(ValidationError):
            case._schedule(case.version - 1, {})
        other = self.user.copy({'login': 'workflow.other', 'name': 'Other'})
        with self.assertRaises(AccessError):
            case.with_user(other)._public()

    def test_automation_schedules_dispatches_and_waits_for_candidate_response(self):
        self.applicant.write({'interviewer_ids': [(6, 0, self.user.ids)]})
        case = self.case()
        case._automation_start(case.version)
        case._auto_schedule(case.version)
        self.assertEqual(case.automation_state, 'scheduling')
        self.assertEqual(case.draft['status'], 'ready')
        case._prepare(case.version, 'en', [
            {'criterion_id': case.criteria[0]['id'], 'question': f'Question {i}?', 'follow_up': None}
            for i in range(5)
        ])
        case._automation_dispatch(case.version)
        session = case.session_ids
        self.assertEqual(case.automation_state, 'awaiting_candidate')
        self.assertEqual(session.status, 'prepared')
        self.assertEqual(session._candidate_public()['current_question'], None)
        session._respond('accepted')
        self.assertEqual(case.automation_state, 'interview')
        self.assertEqual(session.status, 'awaiting_answers')
        self.assertTrue(session._candidate_public()['current_question'])
        session._finish()
        self.assertEqual(case.automation_state, 'reporting')

    def test_schedule_missing_dst_and_no_overlap(self):
        case = self.case()
        case._approve_criteria(case.criteria_version)
        result = case._schedule(case.version, {})
        self.assertEqual(result['schedule']['status'], 'needs_input')
        result = case._schedule(case.version, {'start': '2027-03-14T02:30:00', 'timezone': 'America/New_York'})
        self.assertTrue(result['schedule']['issues'])
        case = self.ready()
        self.assertEqual(case._public()['schedule']['status'], 'ready')
        changed = case._schedule(case.version, {'start': '2027-11-07T01:30:00', 'timezone': 'America/New_York'})
        self.assertEqual(changed['schedule']['status'], 'needs_input')
        self.assertFalse(changed['schedule']['guide_ready'])

    def test_approval_capability_and_booking_idempotency(self):
        case = self.ready()
        with self.assertRaises(AccessError):
            case._confirm(case.draft['version'], None)
        approval = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        first = case._confirm(case.draft['version'], approval)
        second = case._confirm(case.draft['version'], approval)
        with self.assertRaises(ValidationError):
            case._confirm(999, approval)
        self.assertEqual(first['schedule']['booking'], second['schedule']['booking'])
        self.assertEqual(len(case.session_ids), 1)
        self.assertNotIn('token', str(first).lower())
        self.job.write({'requirements': 'Changed after the committed booking'})
        retried = case._approval(approval.draft_version, approval.client_message_id, str(uuid4()))
        self.assertEqual(retried.id, approval.id)
        self.assertEqual(case._confirm(approval.draft_version, retried)['schedule']['booking'], first['schedule']['booking'])
        with self.assertRaises(AccessError):
            case.session_ids.write({'status': 'completed'})

    def test_candidate_answers_idempotent_resumable_and_revoked(self):
        case = self.ready()
        approval = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        case._confirm(case.draft['version'], approval)
        session = case.session_ids
        qid = session.questions[0]['id']
        answer_id = str(uuid4())
        first = session._answer(qid, answer_id, 'My actual answer')
        second = session._answer(qid, answer_id, 'My actual answer')
        self.assertEqual(first, second)
        self.assertEqual(len(session.answer_ids), 1)
        with self.assertRaises(ValidationError):
            session._answer(qid, answer_id, 'Changed answer')
        with self.assertRaises(ValidationError):
            session._answer(session.questions[2]['id'], str(uuid4()), 'Skip a question')
        self.assertEqual(session._candidate_public()['current_question']['id'], session.questions[1]['id'])
        session._finish()
        self.assertEqual(session.status, 'partial')
        case._revoke(session.id)
        with self.assertRaises(AccessError):
            session._candidate_public()

    def test_source_change_invalidates_criteria_and_outputs(self):
        case = self.ready()
        self.job.write({'requirements': 'Changed synthetic requirements'})
        self.job.flush_recordset()
        self.assertFalse(case._public()['criteria_approved'])
        with self.assertRaises(ValidationError):
            case._approval(case.draft['version'], str(uuid4()), str(uuid4()))

    def test_workflow_grant_requires_explicit_applicant_anchor(self):
        run = str(uuid4())
        grant, token = self.bridge._issue_grant(run, [{'model': 'hr.job', 'id': self.job.id}], 'execution')
        result = self.bridge._execute('workflow_context', {'applicant_id': self.applicant.id}, [{'model': 'hr.job', 'id': self.job.id}])
        self.assertFalse(result['ok'])
        refs = [{'model': 'hr.applicant', 'id': self.applicant.id}]
        grant, token = self.bridge._issue_grant(run, refs, 'execution')
        self.assertEqual(self.bridge._verify_grant(token, self.env.cr.dbname, run, 'workflow_context')['uid'], self.user.id)
        with self.assertRaises(AccessError):
            self.bridge._verify_grant(token, self.env.cr.dbname, run, 'workflow_confirm')
        self.assertTrue(self.bridge._execute('workflow_context', {'applicant_id': self.applicant.id}, refs)['ok'])

    def test_mail_is_scoped_and_source_revalidated_before_send(self):
        case = self.ready()
        approval = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        case._confirm(case.draft['version'], approval)
        mail = case.session_ids.mail_id
        self.assertEqual(mail.jobhunter_workflow_session_id, case.session_ids)
        with self.assertRaises(AccessError):
            mail.write({'email_to': 'other@example.invalid'})
        with self.assertRaises(AccessError):
            case.env['mail.mail'].create({'subject': 'Forgery', 'email_to': 'other@example.invalid',
                                          'jobhunter_workflow_session_id': case.session_ids.id})
        self.job.write({'requirements': 'Changed before queued mail send'})
        # No SMTP network call is made: stale source prevents _send from reaching core.
        mail.sudo()._send()
        self.assertEqual(mail.state, 'exception')
        self.assertEqual(case.session_ids._mail_status(), 'not_sent')

    def test_artifacts_quote_validation_versions_and_revoked_dependencies(self):
        case = self.case()
        case._approve_criteria(case.criteria_version)
        sources = case._sources()
        quote = 'Workflow Candidate'
        content = {'summary': 'Evidence checked', 'findings': [
            {'criterion_id': criterion['id'], 'status': 'unknown', 'finding': 'Not established by available evidence',
             'citations': [], 'question': 'Please describe your experience.'} for criterion in case.criteria]}
        case._save(case.version, 'screening', content, sources)
        saved = case._public()['screening']
        self.assertEqual(saved['source_state'], 'current')
        self.assertEqual(saved['version'], 1)
        ref = case.artifact_ids._source(1)
        self.assertEqual(self.bridge._validate_sources([ref])[0]['state'], 'current')
        with self.assertRaises(AccessError):
            case.artifact_ids.write({'content': {'text': 'forged'}})
        bad = {'summary': 'Forged evidence', 'findings': [
            {'criterion_id': criterion['id'], 'status': 'supported', 'finding': 'Unsupported assertion',
             'citations': [{'source_key': 'S1', 'quote': 'This quotation does not exist'}], 'question': ''} for criterion in case.criteria]}
        with self.assertRaises(ValidationError):
            case._save(case.version, 'screening', bad, sources)
        with self.assertRaises(ValidationError):
            case._save(case.version, 'report', {'text': 'Decision [S1]', 'score': 99}, sources)
        self.job.write({'requirements': 'Revised criteria'})
        self.assertFalse(case._public()['criteria_approved'])
        self.assertEqual(case._public()['screening']['source_state'], 'outdated')
        self.assertIsNone(case._public()['screening']['content'])
        self.assertEqual(self.bridge._validate_sources([ref])[0]['state'], 'outdated')

    def test_email_scope_and_confirmation_preview_change(self):
        case = self.ready()
        approval = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        old_version = case.draft['version']
        case._schedule(case.version, {'duration_minutes': 45})
        with self.assertRaises(ValidationError):
            case._confirm(old_version, approval)
        self.assertFalse(case.session_ids)
        with self.assertRaises(AccessError):
            case.env['mail.mail'].create({'subject': 'Unrelated forgery', 'email_to': 'other@example.invalid'})

    def test_dst_gap_fold_no_overlap_and_missing_participant_zone(self):
        from ..models.workflow_schedule import validate_schedule
        case = self.ready()
        original = case.draft['schedule']
        for local in ('2027-03-14T02:30:00', '2027-11-07T01:30:00'):
            result = validate_schedule({**original, 'start': local, 'timezone': 'America/New_York'})
            self.assertEqual(result['status'], 'needs_input')
            self.assertTrue(any('DST' in issue for issue in result['issues']))
        participant = {**original['participants'][0], 'availability': [{'start': '2027-01-01T09:00:00', 'end': '2027-01-01T10:00:00'}]}
        result = validate_schedule({**original, 'participants': [participant, original['participants'][1]]})
        self.assertTrue(any('does not fit' in issue for issue in result['issues']))
        participant = {**original['participants'][0], 'timezone': ''}
        result = validate_schedule({**original, 'participants': [participant, original['participants'][1]]})
        self.assertEqual(result['status'], 'needs_input')

    def test_expiry_owner_revocation_and_finish_completeness(self):
        case = self.ready()
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        session = case.session_ids
        for question in session.questions:
            session._answer(question['id'], str(uuid4()), 'A real explicitly submitted answer')
        self.assertEqual(session.status, 'in_progress')
        self.assertIsNone(session._candidate_public()['current_question'])
        session._finish()
        self.assertEqual(session.status, 'completed')
        session._mutate({'expires_at': fields.Datetime.now() - timedelta(seconds=1)})
        with self.assertRaises(AccessError):
            session._candidate_public()
        self.assertEqual(session._summary()['status'], 'expired')

    def test_unconfigured_production_send_has_no_business_sideeffects(self):
        from unittest.mock import patch
        case = self.ready()
        approval = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        before = self.env['calendar.event'].search_count([])
        with patch.dict('os.environ', {'JOBHUNTER_TEST_CAPTURE': '0'}):
            with self.assertRaisesRegex(ValidationError, 'public HTTPS'):
                case._confirm(case.draft['version'], approval)
        self.assertEqual(self.env['calendar.event'].search_count([]), before)
        self.assertFalse(case.session_ids)

    def test_smtp_ambiguous_acceptance_is_not_retried(self):
        import smtplib
        from unittest.mock import patch, Mock
        from odoo.tools import mute_logger
        case = self.ready()
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        mail = case.session_ids.mail_id.sudo()
        with patch.object(type(self.env['ir.mail_server']), '_disable_send', return_value=False), patch.object(type(self.env['ir.mail_server']), 'send_email', side_effect=smtplib.SMTPServerDisconnected('Synthetic ambiguous acceptance')) as send:
            with mute_logger('odoo.addons.mail.models.mail_mail'):
                with self.assertRaises(smtplib.SMTPServerDisconnected):
                    mail._send(smtp_session=Mock())
            self.assertEqual(case.session_ids._mail_status(), 'unknown')
            mail._send(smtp_session=Mock())
            self.assertEqual(send.call_count, 1)
            self.assertEqual(case.session_ids._mail_status(), 'unknown')

    def test_scoped_callback_cannot_confirm_without_browser_grant(self):
        from unittest.mock import patch, Mock
        from ..controllers.chat import JobhunterChat
        from ..controllers import chat
        case = self.ready()
        refs = [{'model': 'hr.applicant', 'id': self.applicant.id}]
        run = str(uuid4())
        grant, token = self.bridge._issue_grant(run, refs, 'execution')
        req = Mock(env=self.bridge.env)
        req.httprequest.headers = {'Authorization': 'Bearer ' + token, 'X-Odoo-Database': self.env.cr.dbname}
        req.update_env.side_effect = lambda **kw: None
        with patch.object(chat, 'request', req):
            result = JobhunterChat().tool(run, 'workflow_confirm', {'arguments': {'case_id': case.id, 'draft_version': case.draft['version']}, 'context_refs': refs})
            self.assertEqual(result['error']['code'], 'access_denied')
        approval = case._approval(case.draft['version'], str(uuid4()), run)
        grant, token = self.bridge._issue_grant(run, refs, 'execution', workflow={'applicant_id': self.applicant.id, 'approval_id': approval.id})
        req.httprequest.headers['Authorization'] = 'Bearer ' + token
        with patch.object(chat, 'request', req):
            result = JobhunterChat().tool(run, 'workflow_confirm', {'arguments': {'case_id': case.id, 'draft_version': case.draft['version']}, 'context_refs': refs})
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['data']['case']['schedule']['booking'])
            self.assertTrue(any(source['model'] == 'jobhunter.chat.artifact' for source in result['sources']))
            self.assertTrue(all(item['state'] == 'current' for item in self.bridge._validate_sources(result['sources'], refs)))

    def test_mail_creation_failure_rolls_back_event_and_session(self):
        from unittest.mock import patch
        case = self.ready()
        approval = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        before = self.env['calendar.event'].search_count([])
        with patch.object(type(case.env['mail.mail']), 'create', side_effect=ValidationError('Synthetic queue failure')):
            with self.assertRaises(ValidationError):
                case._confirm(case.draft['version'], approval)
        self.assertFalse(case.session_ids)
        self.assertFalse(approval.session_id)
        self.assertEqual(self.env['calendar.event'].search_count([]), before)
        self.assertFalse(case.draft.get('booking'))

    def test_candidate_page_is_renderable_and_csrf_guarded(self):
        from unittest.mock import patch, Mock
        from ..controllers import workflow
        from ..controllers.workflow import CandidateInterview
        rendered = self.env['ir.ui.view']._render_template('t10_jobhunter_chat.candidate_interview', {'csrf': 'synthetic-csrf', 'nonce': 'synthetic-nonce'})
        self.assertIn('data-csrf="synthetic-csrf"', str(rendered))
        self.assertIn('crypto.randomUUID()', str(rendered))
        self.assertNotIn('https://', str(rendered))
        req = Mock(env=self.bridge.env)
        req.validate_csrf.return_value = False
        with patch.object(workflow, 'request', req):
            self.assertEqual(CandidateInterview().rpc('invalid', 'answer', {}, 'bad')['error']['code'], 'csrf_failed')

    def test_direct_rpc_read_hides_stale_artifact_content(self):
        case = self.case()
        case._approve_criteria(case.criteria_version)
        content = {'summary': 'Synthetic', 'findings': [{'criterion_id': item['id'], 'status': 'unknown',
                    'finding': 'Unverified', 'citations': [], 'question': ''} for item in case.criteria]}
        case._save(case.version, 'screening', content, case._sources())
        artifact = case.artifact_ids
        self.assertTrue(artifact.read(['content'])[0]['content'])
        self.job.write({'requirements': 'Changed synthetic job'})
        with self.assertRaises(AccessError):
            artifact.read(['content'])

    def test_date_without_clock_is_not_an_exact_local_datetime(self):
        from ..models.workflow_schedule import local_time
        with self.assertRaises(ValueError):
            local_time('2027-01-01', 'UTC')

    def test_search_read_revalidates_artifact_session_and_answer_sources(self):
        case = self.ready()
        content = {'summary': 'Synthetic', 'findings': [{'criterion_id': item['id'], 'status': 'unknown',
                    'finding': 'Unverified', 'citations': [], 'question': ''} for item in case.criteria]}
        case._save(case.version, 'screening', content, case._sources())
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        session = case.session_ids
        session._answer(session.questions[0]['id'], str(uuid4()), 'Actual answer retained privately')
        targets = [(case.artifact_ids.filtered(lambda item: item.kind == 'screening'), ['content', 'sources']),
                   (session, ['questions', 'dependencies']), (session.answer_ids, ['text', 'question'])]
        for record, payload in targets:
            self.assertTrue(record.search_read([('id', '=', record.id)], payload))
        case._revoke(session.id)
        for record, payload in targets[1:]:
            with self.subTest(revoked_model=record._name):
                with self.assertRaises(AccessError):
                    record.search_read([('id', '=', record.id)], payload)
        self.job.write({'requirements': 'Revoked original source version'})
        for record, payload in targets:
            with self.subTest(model=record._name):
                with self.assertRaises(AccessError):
                    record.search_read([('id', '=', record.id)], payload)
                with self.assertRaises(AccessError):
                    record.web_read({field: {} for field in payload})
        with self.assertRaises(AccessError):
            session.answer_ids.read_group([('id', 'in', session.answer_ids.ids)], ['question'], ['__count'])
        self.user.group_ids |= self.env.ref('base.group_allow_export')
        with self.assertRaises(AccessError):
            targets[0][0].export_data(['content'])

    def test_new_confirmation_uuid_cannot_duplicate_same_draft(self):
        case = self.ready()
        first = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        before_events = self.env['calendar.event'].search_count([])
        original = case._confirm(case.draft['version'], first)
        second = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        repeated = case._confirm(case.draft['version'], second)
        after = case._approval(case.draft['version'], str(uuid4()), str(uuid4()))
        third = case._confirm(case.draft['version'], after)
        self.assertEqual(len(case.session_ids), 1)
        self.assertEqual(self.env['calendar.event'].search_count([]), before_events + 1)
        self.assertEqual(original['schedule']['booking'], repeated['schedule']['booking'])
        self.assertEqual(original['schedule']['booking'], third['schedule']['booking'])
        self.assertEqual(case.env['mail.mail'].search_count([('jobhunter_workflow_session_id', 'in', case.session_ids.ids)]), 1)

    def test_mail_default_context_cannot_forge_approved_session_invitation(self):
        case = self.ready()
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        session = case.session_ids
        mail_model = case.env['mail.mail'].with_context(default_jobhunter_workflow_session_id=session.id)
        before = self.env['mail.mail'].search_count([])
        with self.assertRaises(AccessError):
            mail_model.create({'subject': 'Forged invitation', 'email_to': 'arbitrary@example.invalid', 'body_html': '<p>Forged body</p>'})
        self.assertEqual(self.env['mail.mail'].search_count([]), before)
        # Supplying explicit False must also be evaluated after effective defaults;
        # ordinary superuser mail without a workflow marker retains core behavior.
        ordinary = self.env['mail.mail'].sudo().create({'subject': 'Unrelated core queue', 'email_from': 'sender@example.invalid', 'email_to': 'core@example.invalid', 'body_html': '<p>Core</p>'})
        self.assertFalse(ordinary.jobhunter_workflow_session_id)
        self.assertEqual(ordinary.state, 'outgoing')
        from unittest.mock import patch, Mock
        with patch.object(type(self.env['ir.mail_server']), '_disable_send', return_value=False), patch.object(type(self.env['ir.mail_server']), 'send_email', return_value='<synthetic-core@example.invalid>') as transport:
            ordinary._send(smtp_session=Mock())
            transport.assert_called_once()
        self.assertEqual(ordinary.state, 'sent')
        self.assertIsNone(ordinary._workflow_delivery_state())


    def test_search_read_rechecks_original_applicant_access(self):
        case = self.ready()
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        session = case.session_ids
        session._answer(session.questions[0]['id'], str(uuid4()), 'Private evidence')
        self.env['ir.access'].create({'name': 'Synthetic revoked applicant read',
            'model_id': self.env['ir.model']._get_id('hr.applicant'), 'operation': 'r',
            'domain': repr([('id', '!=', self.applicant.id)])})
        for record, payload in ((case, ['criteria', 'source_snapshot']), (session, ['questions']), (session.answer_ids, ['text'])):
            with self.subTest(model=record._name):
                with self.assertRaises(AccessError):
                    record.search_read([('id', '=', record.id)], payload)

    def test_noncanonical_linked_mail_cannot_reach_transport(self):
        from unittest.mock import patch, Mock
        from ..models.workflow import _INTERNAL
        case = self.ready()
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        # Simulate an extra mail persisted before the guard fix. Even server-side
        # linked metadata cannot authorize a second candidate invitation.
        extra = case.env['mail.mail'].with_context(_jobhunter_workflow=_INTERNAL).create({
            'jobhunter_workflow_session_id': case.session_ids.id, 'subject': 'Legacy extra',
            'email_to': 'extra@example.invalid', 'body_html': '<p>Must not send</p>'})
        with patch.object(type(self.env['ir.mail_server']), '_disable_send', return_value=False), patch.object(type(self.env['ir.mail_server']), 'send_email') as transport:
            extra.sudo()._send(smtp_session=Mock())
            transport.assert_not_called()
        self.assertEqual(extra.state, 'exception')
        self.assertNotEqual(case.session_ids.mail_id.id, extra.id)


    def _submit_schedule_controller(self, data):
        from unittest.mock import patch, Mock
        from ..controllers.chat import JobhunterChat
        from ..controllers import chat
        controller = JobhunterChat()
        req = Mock(env=self.bridge.env)
        req.validate_csrf.return_value = True
        args = {'conversation_id': str(uuid4()), 'client_message_id': str(uuid4()), 'text': 'Prepare interview schedule',
                'context_refs': [{'model': 'hr.applicant', 'id': self.applicant.id}],
                'workflow': {'action': 'schedule', 'applicant_id': self.applicant.id, 'data': data}}
        with patch.object(chat, 'request', req), patch.object(controller, '_scope', return_value=(self.bridge, self.bridge._principal())), patch.object(controller, '_grant', return_value='synthetic-capability') as grant, patch.object(controller, '_proxy', return_value={'accepted': True}) as proxy:
            result = controller.rpc('messages.send', args, csrf_token='synthetic-csrf')
        return result, grant, proxy

    def test_schedule_controller_accepts_valid_and_empty_payload(self):
        participant = {'name': 'Candidate', 'email': 'candidate@example.invalid', 'timezone': 'UTC', 'availability': [
            {'start': '2027-01-01T09:00:00', 'end': '2027-01-01T10:00:00'}]}
        schedule = {'start': '2027-01-01T09:00:00', 'timezone': 'UTC', 'duration_minutes': 30, 'participant_count': 1,
                    'candidate_email': 'candidate@example.invalid', 'participants': [participant]}
        for data in ({}, {'schedule': {}}, {'schedule': schedule}, {'schedule': {'start': None, 'participants': None}}, {'schedule': {'duration_minutes': 45}}):
            with self.subTest(data=data):
                result, grant, proxy = self._submit_schedule_controller(data)
                self.assertEqual(result, {'accepted': True})
                grant.assert_called_once()
                proxy.assert_called_once()
                self.assertEqual(proxy.call_args.args[3]['workflow']['data'], data)

    def test_schedule_controller_rejects_unknown_malformed_and_unbounded_payload(self):
        participant = {'name': 'Candidate', 'email': 'candidate@example.invalid', 'timezone': 'UTC', 'availability': []}
        malformed = [
            {'unknown': {}}, {'schedule': {}, 'unknown': 1}, {'schedule': None}, {'schedule': []},
            {'schedule': {'unknown': 1}}, {'schedule': {'duration_minutes': True}}, {'schedule': {'duration_minutes': 14}},
            {'schedule': {'duration_minutes': 181}}, {'schedule': {'participant_count': 0}}, {'schedule': {'participant_count': 11}},
            {'schedule': {'start': 123}}, {'schedule': {'start': 'x' * 65}}, {'schedule': {'timezone': 'x' * 101}},
            {'schedule': {'candidate_email': 'x' * 255}}, {'schedule': {'participants': [participant] * 11}},
            {'schedule': {'participants': [{}]}}, {'schedule': {'participants': [{**participant, 'unknown': 1}]}},
            {'schedule': {'participants': [{**participant, 'user_id': True}]}}, {'schedule': {'participants': [{**participant, 'user_id': 1}]}},
            {'schedule': {'participants': [{**participant, 'name': 'x' * 201}]}},
            {'schedule': {'participants': [{**participant, 'availability': [{'start': 'x', 'end': 'y'}] * 21}]}},
            {'schedule': {'participants': [{**participant, 'availability': [{'start': 'x', 'end': 'y', 'unknown': 1}]}]}},
            {'schedule': {'participants': [{**participant, 'availability': [{'start': 'x' * 65, 'end': 'y'}]}]}},
            {'schedule': {'participants': [{**participant, 'availability': [{'start': 'x' * 64, 'end': 'y' * 64}] * 20}] * 10}},
        ]
        for data in malformed:
            with self.subTest(data=data):
                result, grant, proxy = self._submit_schedule_controller(data)
                self.assertEqual(result.get('error', {}).get('code'), 'invalid_arguments')
                grant.assert_not_called()
                proxy.assert_not_called()


    def direct(self, case=None):
        case = case or self.case()
        case._approve_criteria(case.criteria_version)
        result = self.bridge._workflow_execute('workflow_prepare_direct', {
            'case_id': case.id, 'expected_version': case.version, 'language': 'vi',
            'questions': [{'criterion_id': case.criteria[0]['id'], 'question': f'Direct question {index}?'} for index in range(5)]
        }, [{'model': 'hr.applicant', 'id': self.applicant.id}])
        self.assertTrue(result['ok'])
        return case

    def test_direct_guide_requires_current_approved_criteria_and_exactly_five_questions(self):
        case = self.case()
        questions = [{'criterion_id': case.criteria[0]['id'], 'question': 'Describe your experience.'} for _ in range(5)]
        with self.assertRaises(ValidationError):
            case._prepare_direct(case.version, 'en', questions)
        case._approve_criteria(case.criteria_version)
        for language, invalid in [('en', questions[:4]), ('en', [{**questions[0], 'criterion_id': 'C999'}] * 5),
                                  ('fr', questions), ('en', [{**questions[0], 'follow_up': ['two', 'items']}] * 5),
                                  ('en', [{**questions[0], 'extra': True}] * 5)]:
            with self.subTest(language=language, questions=invalid):
                with self.assertRaises(ValidationError):
                    case._prepare_direct(case.version, language, invalid)
        result = case._prepare_direct(case.version, 'en', questions)
        self.assertIsNone(result['schedule'])
        self.assertEqual(result['direct_guide']['version'], 1)
        self.assertEqual(len(result['direct_guide']['questions']), 5)
        self.assertIsNone(result['direct_guide']['questions'][0]['follow_up'])
        with self.assertRaises(ValidationError):
            case._prepare_direct(case.version - 1, 'en', questions)

    def test_direct_creation_is_private_retry_safe_and_has_no_calendar_or_mail(self):
        import hashlib
        case = self.direct()
        events = self.env['calendar.event'].search_count([])
        mails = self.env['mail.mail'].search_count([])
        request_id = str(uuid4())
        first = case._create_direct(1, request_id)
        repeated = case._create_direct(1, request_id)
        self.assertEqual(first['session_id'], repeated['session_id'])
        self.assertTrue(first['interview_url'] == repeated['interview_url'], 'Retry must preserve its private URL.')
        self.assertEqual(first['case'], repeated['case'])
        self.assertEqual(len(case.session_ids), 1)
        session = case.session_ids
        self.assertEqual(session.guide_version, 1)
        self.assertEqual(session.client_request_id, request_id)
        self.assertFalse(session.event_id)
        self.assertFalse(session.mail_id)
        self.assertEqual(self.env['calendar.event'].search_count([]), events)
        self.assertEqual(self.env['mail.mail'].search_count([]), mails)
        token = first['interview_url'].rsplit('/', 1)[1]
        self.assertEqual(session.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertFalse(token in str(case._public()), 'Public case must not contain the bearer.')
        self.assertFalse(token in str(case._sources()), 'Sources must not contain the bearer.')
        self.assertFalse(token in str(session.read()[0]), 'ORM payload must not contain the bearer.')
        self.assertLessEqual(session.expires_at, fields.Datetime.now() + timedelta(days=7))
        self.assertGreater(session.expires_at, fields.Datetime.now() + timedelta(days=6))
        with self.assertRaises(ValidationError):
            case._create_direct(1, str(uuid4()))
        with self.assertRaises(ValidationError):
            case._create_direct(2, request_id)
        with self.assertRaises(ValidationError):
            case._create_direct(1, 'not-a-uuid')
        other = self.user.copy({'login': 'direct.other', 'name': 'Other'})
        with self.assertRaises(AccessError):
            case.with_user(other)._create_direct(1, request_id)
        session._answer(session.questions[0]['id'], str(uuid4()), 'An actual typed answer')
        self.assertEqual(session._candidate_public()['submitted_count'], 1)

    def test_direct_guide_revisions_preserve_booked_and_prior_session_questions(self):
        case = self.ready()
        booked_guide = dict(case.guide)
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        booked = case.session_ids
        booked_questions = list(booked.questions)
        self.direct(case)
        first_request = str(uuid4())
        created = case._create_direct(1, first_request)
        self.direct(case)
        second = case._create_direct(2, str(uuid4()))
        self.assertNotEqual(created['session_id'], second['session_id'])
        self.assertEqual(case.guide, booked_guide)
        self.assertEqual(booked.questions, booked_questions)
        self.assertTrue(case._create_direct(1, first_request)['interview_url'] == created['interview_url'], 'Earlier session retries keep their private URL.')
        self.assertEqual(len(case.session_ids), 3)
        self.assertEqual(case._public()['schedule']['booking']['mail_status'], booked._mail_status())
        self.job.write({'requirements': 'Current source changed after direct preparation'})
        self.assertIsNone(case._public()['direct_guide'])
        for session in case.session_ids:
            with self.assertRaises(AccessError):
                session._candidate_public()
        with self.assertRaises(AccessError):
            case._create_direct(1, first_request)
        self.direct(case)
        self.assertEqual(case._public()['direct_guide']['version'], 3)

    def test_direct_browser_operation_enforces_csrf_and_keeps_model_boundary_link_free(self):
        from unittest.mock import patch, Mock
        from ..controllers import chat
        from ..controllers.chat import JobhunterChat
        case = self.direct()
        req = Mock(env=self.bridge.env)
        req.validate_csrf.return_value = True
        controller = JobhunterChat()
        args = {'case_id': case.id, 'guide_version': 1, 'client_request_id': str(uuid4())}
        with patch.object(chat, 'request', req), patch.object(controller, '_scope', return_value=(self.bridge, self.bridge._principal())):
            result = controller.rpc('workflow.interview.create', args, csrf_token='synthetic-csrf')
            self.assertIn('interview_url', result)
            req.validate_csrf.return_value = False
            self.assertEqual(controller.rpc('workflow.interview.create', args, csrf_token='bad')['error']['code'], 'csrf_failed')
        run = str(uuid4())
        refs = [{'model': 'hr.applicant', 'id': self.applicant.id}]
        grant, token = self.bridge._issue_grant(run, refs, 'execution', workflow={'applicant_id': self.applicant.id, 'approval_id': None})
        self.assertEqual(self.bridge._verify_grant(token, self.env.cr.dbname, run, 'workflow_prepare_direct')['uid'], self.user.id)
        with self.assertRaises(AccessError):
            self.bridge._verify_grant(token, self.env.cr.dbname, run, 'workflow.interview.create')
        with self.assertRaises(AccessError):
            self.bridge._workflow_execute('workflow_prepare_direct', {'case_id': case.id, 'expected_version': case.version,
                'language': 'en', 'questions': []}, [], scoped_applicant=self.applicant.id)

    def artifact_dag(self):
        case = self.case()
        case._approve_criteria(case.criteria_version)
        base = list(case.source_snapshot)
        artifacts = []
        for index in range(6):
            dependencies = list(base)
            for artifact in artifacts:
                dependencies.append(artifact._source(len(dependencies) + 1))
            artifact = case.env['jobhunter.chat.artifact']._insert({
                'case_id': case.id, 'owner_id': self.user.id, 'company_id': self.company.id,
                'kind': 'report', 'content': {'text': 'Synthetic shared evidence [S1]'},
                'sources': dependencies, 'version': index + 1, 'fingerprint': case.source_fingerprint,
                'criteria_version': case.criteria_version})
            artifacts.append(artifact)
        return case, artifacts

    def test_shared_artifact_dag_validates_each_source_identity_once_per_scope(self):
        from unittest.mock import patch
        case, artifacts = self.artifact_dag()
        references = [artifact._source(index + 1) for index, artifact in enumerate(artifacts)]
        original = type(self.bridge)._workflow_source_state
        resolved = []
        def track(bridge, source, anchors):
            resolved.append(source['record_id'])
            return original(bridge, source, anchors)
        with patch.object(type(self.bridge), '_workflow_source_state', track):
            states = self.bridge._validate_sources(references)
        self.assertEqual([item['state'] for item in states], ['current'] * 6)
        self.assertLessEqual(len(resolved), 6, 'Shared dependencies must not be resolved for every DAG path.')

    def test_validation_memo_cannot_reuse_stale_state_between_top_level_calls(self):
        case, artifacts = self.artifact_dag()
        source = artifacts[-1]._source(1)
        self.assertEqual(self.bridge._validate_sources([source])[0]['state'], 'current')
        self.job.write({'requirements': 'Changed after the preceding validation call'})
        self.assertEqual(self.bridge._validate_sources([source])[0]['state'], 'outdated')

    def test_validation_memo_preserves_source_identity_scope_and_depth_checks(self):
        case, artifacts = self.artifact_dag()
        source = artifacts[0]._source(1)
        forged = {**source, 'key': 'S2', 'excerpt': 'Forged artifact content'}
        result = self.bridge._validate_sources([source, forged, {**source, 'key': 'S3'}])
        self.assertEqual([item['state'] for item in result], ['current', 'unavailable', 'current'])
        self.assertEqual(self.bridge.with_context(_workflow_source_visiting=(artifacts[0].id,))._validate_sources([source])[0]['state'], 'unavailable')
        self.assertEqual(self.bridge.with_context(_workflow_source_visiting=tuple(range(-40, 0)))._validate_sources([source])[0]['state'], 'unavailable')
        other = self.user.copy({'login': 'memo.other', 'name': 'Other recruiter'})
        self.assertEqual(self.bridge.with_user(other)._validate_sources([source])[0]['state'], 'unavailable')
        # Warm a shallow leaf in the same call that later reaches that leaf at
        # forbidden depth. Reusing its cached current result must not bypass40.
        head = artifacts[0]
        for index in range(40):
            head = case.env['jobhunter.chat.artifact']._insert({
                'case_id': case.id, 'owner_id': self.user.id, 'company_id': self.company.id,
                'kind': 'report', 'content': {'text': 'Synthetic depth guard [S1]'},
                'sources': [head._source(1)], 'version': index + 7,
                'fingerprint': case.source_fingerprint, 'criteria_version': case.criteria_version})
        result = self.bridge._validate_sources([source, head._source(2)])
        self.assertEqual([item['state'] for item in result], ['current', 'unavailable'])

    def test_expired_answered_session_does_not_block_new_direct_workflow(self):
        case = self.direct()
        created = case._create_direct(1, str(uuid4()))
        session = case.session_ids.filtered(lambda item: item.id == created['session_id'])
        session._answer(session.questions[0]['id'], str(uuid4()), 'An actual answer before expiry')
        session._mutate({'expires_at': fields.Datetime.now() - timedelta(seconds=1)})
        self.direct(case)
        self.assertEqual(case._public()['direct_guide']['version'], 2)
        sources = case._sources()
        self.assertTrue(any(source['model'] == 'hr.applicant' for source in sources))
        self.assertFalse(any(source['model'] == 'jobhunter.chat.answer' for source in sources))
        with self.assertRaises(AccessError):
            session._candidate_public()
        other = self.user.copy({'login': 'expired.direct.other', 'name': 'Other recruiter'})
        with self.assertRaises(AccessError):
            case.with_user(other)._sources()

    def test_unavailable_answer_dependencies_are_skipped_without_hiding_unrelated_errors(self):
        from unittest.mock import patch
        case = self.direct()
        case._create_direct(1, str(uuid4()))
        session = case.session_ids
        session._answer(session.questions[0]['id'], str(uuid4()), 'Answer with current source dependencies')
        with patch.object(type(session.answer_ids), '_source', side_effect=RuntimeError('Synthetic unexpected error')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic unexpected error'):
                case._sources()
        dependencies = [{**source, 'write_date': '2000-01-01T00:00:00Z'} for source in session.dependencies]
        session._mutate({'dependencies': dependencies})
        sources = case._sources()
        self.assertTrue(any(source['model'] == 'hr.applicant' for source in sources))
        self.assertFalse(any(source['model'] == 'jobhunter.chat.answer' for source in sources))

    def test_current_direct_interview_remains_addressable_after_newer_booking(self):
        case = self.direct()
        self.assertIsNone(case._public()['direct_interview'])
        created = case._create_direct(1, str(uuid4()))
        case = self.ready()
        case._confirm(case.draft['version'], case._approval(case.draft['version'], str(uuid4()), str(uuid4())))
        public = case._public()
        self.assertNotEqual(public['interview']['id'], created['session_id'])
        self.assertEqual(public['direct_interview']['id'], created['session_id'])
        self.assertEqual(public['direct_interview']['guide_version'], 1)
        self.direct(case)
        self.assertIsNone(case._public()['direct_interview'])
        newer = case._create_direct(2, str(uuid4()))
        self.assertEqual(case._public()['direct_interview']['id'], newer['session_id'])

    def test_prepare_interview_submission_accepts_only_empty_data(self):
        from unittest.mock import patch, Mock
        from ..controllers import chat
        from ..controllers.chat import JobhunterChat
        req = Mock(env=self.bridge.env)
        req.validate_csrf.return_value = True
        controller = JobhunterChat()
        args = {'conversation_id': str(uuid4()), 'client_message_id': str(uuid4()), 'text': 'Prepare interview',
                'context_refs': [{'model': 'hr.applicant', 'id': self.applicant.id}],
                'workflow': {'action': 'prepare_interview', 'applicant_id': self.applicant.id, 'data': {}}}
        with patch.object(chat, 'request', req), patch.object(controller, '_scope', return_value=(self.bridge, self.bridge._principal())), patch.object(controller, '_grant', return_value='synthetic-capability'), patch.object(controller, '_proxy', return_value={'accepted': True}):
            self.assertEqual(controller.rpc('messages.send', args, csrf_token='synthetic-csrf'), {'accepted': True})
            args['workflow']['data'] = {'guide_version': 1}
            self.assertEqual(controller.rpc('messages.send', args, csrf_token='synthetic-csrf')['error']['code'], 'invalid_arguments')


@tagged('post_install', '-at_install')
class TestCandidateWorkflowConcurrency(BaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from unittest.mock import patch
        from odoo.orm.registry import Registry
        cls.registry = Registry(get_db_name())
        registry = cls.registry
        # Same in-process registry signaling hooks used by TransactionCase, but
        # without its long-lived transaction (which would lock user fixtures).
        original_sequence, original_caches = registry.registry_sequence, registry.registry_caches__.copy()
        cls.addClassCleanup(setattr, registry, 'registry_sequence', original_sequence)
        cls.addClassCleanup(setattr, registry, 'registry_caches__', original_caches)
        def sequences(cr):
            return registry.registry_sequence, {name: value[0] for name, value in registry.registry_caches__.items()}
        def signal(cr, names):
            if 'registry' in names:
                registry.registry_sequence += 1
                for name, (sequence, data) in list(registry.registry_caches__.items()):
                    registry.registry_caches__[name] = (sequence + 1, {})
            else:
                for name in names:
                    sequence, data = registry.registry_caches__[name]
                    registry.registry_caches__[name] = (sequence + 1, {})
        cls.startClassPatcher(patch.object(registry, 'get_sequences', sequences))
        cls.startClassPatcher(patch.object(registry, '_signal_changes', signal))

    def test_independent_transactions_confirm_one_case_draft_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier, RLock
        from unittest.mock import patch
        from odoo.orm.registry import Registry
        from odoo import api, SUPERUSER_ID
        if not get_db_name().startswith(('jobhunter_e2e', 'jobhunter_baseline')):
            self.skipTest('Committed concurrency fixture is restricted to disposable workflow test databases.')
        registry = self.registry
        # Independent committed synthetic fixture: transactional test fixtures are
        # otherwise invisible to the two real PostgreSQL connections under test.
        with registry.cursor() as cr:
            setup = api.Environment(cr, SUPERUSER_ID, {})
            company = setup['res.company'].create({'name': 'Synthetic race ' + str(uuid4())})
            user = setup['res.users'].create({'name': 'Race Recruiter', 'login': 'race.' + str(uuid4()),
                'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
                'group_ids': [(6, 0, [setup.ref('hr_recruitment.group_hr_recruitment_user').id])]})
            job = setup['hr.job'].create({'name': 'Race Engineer', 'company_id': company.id, 'requirements': 'Synthetic Python requirement'})
            applicant = setup['hr.applicant'].create({'partner_name': 'Race Candidate', 'job_id': job.id, 'email_from': 'race.candidate@example.invalid'})
            owner = api.Environment(cr, user.id, {'allowed_company_ids': company.ids}, su=False)
            case = owner['jobhunter.chat.case']._for_applicant(applicant.id)
            case._approve_criteria(case.criteria_version)
            start = (fields.Datetime.now() + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
            attendees = [{'name': name, 'email': email, 'timezone': 'UTC', 'user_id': None,
                          'availability': [{'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat()}]}
                         for name, email in [('Candidate', 'race.candidate@example.invalid'), ('Interviewer', 'race.interviewer@example.invalid')]]
            case._schedule(case.version, {'start': start.isoformat(), 'timezone': 'UTC', 'duration_minutes': 30,
                'participant_count': 2, 'candidate_email': 'race.candidate@example.invalid', 'participants': attendees})
            case._prepare(case.version, 'en', [{'criterion_id': case.criteria[0]['id'], 'question': f'Race question {index}', 'follow_up': None} for index in range(5)])
            uid, company_id, case_id, draft_version = user.id, company.id, case.id, case.draft['version']
            cr.commit()
        barrier = Barrier(2)
        def confirm_once():
            from psycopg2.errors import SerializationFailure
            barrier.wait(timeout=15)
            client_id, run_id = str(uuid4()), str(uuid4())
            # Match Odoo's HTTP transaction semantics: a serialization failure
            # discards the snapshot and retries the request with the same IDs.
            for attempt in range(3):
                try:
                    with registry.cursor() as cr:
                        env = api.Environment(cr, uid, {'allowed_company_ids': [company_id]}, su=False)
                        case = env['jobhunter.chat.case'].browse(case_id)
                        approval = case._approval(draft_version, client_id, run_id)
                        result = case._confirm(draft_version, approval)['schedule']['booking']
                        cr.commit()
                        return result
                except SerializationFailure:
                    if attempt == 2:
                        raise
        # Native module loading holds Registry._lock on the test thread. Use
        # a test-local registry mutex so worker environments can acquire the
        # already loaded registry; database/row locks remain real and unchanged.
        from odoo.tools import mute_logger
        with mute_logger('odoo.sql_db'), patch.object(Registry, '_lock', RLock()), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm_once) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(results[0], results[1])
        with registry.cursor() as cr:
            owner = api.Environment(cr, uid, {'allowed_company_ids': [company_id]}, su=False)
            case = owner['jobhunter.chat.case'].browse(case_id)
            self.assertEqual(len(case.session_ids), 1)
            self.assertEqual(owner['mail.mail'].search_count([('jobhunter_workflow_session_id', 'in', case.session_ids.ids)]), 1)
            self.assertEqual(owner['calendar.event'].search_count([('id', '=', results[0]['event_id'])]), 1)


    def test_independent_transactions_create_one_direct_session_for_same_request(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier, RLock
        from unittest.mock import patch
        from odoo.orm.registry import Registry
        from odoo import api, SUPERUSER_ID
        from odoo.tools import mute_logger
        if not get_db_name().startswith(('jobhunter_e2e', 'jobhunter_baseline')):
            self.skipTest('Committed concurrency fixture is restricted to disposable workflow test databases.')
        registry = self.registry
        with registry.cursor() as cr:
            setup = api.Environment(cr, SUPERUSER_ID, {})
            company = setup['res.company'].create({'name': 'Synthetic direct race ' + str(uuid4())})
            user = setup['res.users'].create({'name': 'Direct Race Recruiter', 'login': 'direct.race.' + str(uuid4()),
                'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
                'group_ids': [(6, 0, [setup.ref('hr_recruitment.group_hr_recruitment_user').id])]})
            job = setup['hr.job'].create({'name': 'Direct Race Engineer', 'company_id': company.id, 'requirements': 'Synthetic Python requirement'})
            applicant = setup['hr.applicant'].create({'partner_name': 'Direct Race Candidate', 'job_id': job.id})
            owner = api.Environment(cr, user.id, {'allowed_company_ids': company.ids}, su=False)
            case = owner['jobhunter.chat.case']._for_applicant(applicant.id)
            case._approve_criteria(case.criteria_version)
            case._prepare_direct(case.version, 'en', [{'criterion_id': case.criteria[0]['id'], 'question': f'Direct race question {index}'} for index in range(5)])
            uid, company_id, case_id = user.id, company.id, case.id
            cr.commit()
        barrier = Barrier(2)
        client_request_id = str(uuid4())
        def create_once():
            from psycopg2.errors import SerializationFailure
            barrier.wait(timeout=15)
            for attempt in range(3):
                try:
                    with registry.cursor() as cr:
                        env = api.Environment(cr, uid, {'allowed_company_ids': [company_id]}, su=False)
                        result = env['jobhunter.chat.case'].browse(case_id)._create_direct(1, client_request_id)
                        cr.commit()
                        return result
                except SerializationFailure:
                    if attempt == 2:
                        raise
        with mute_logger('odoo.sql_db'), patch.object(Registry, '_lock', RLock()), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create_once) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(results[0]['session_id'], results[1]['session_id'])
        self.assertTrue(results[0]['interview_url'] == results[1]['interview_url'], 'Concurrent retry must preserve its private URL.')
        with registry.cursor() as cr:
            owner = api.Environment(cr, uid, {'allowed_company_ids': [company_id]}, su=False)
            case = owner['jobhunter.chat.case'].browse(case_id)
            self.assertEqual(len(case.session_ids), 1)
            self.assertFalse(case.session_ids.mail_id)
            self.assertFalse(case.session_ids.event_id)
