"""DISPOSABLE TEST APPLICATION ONLY. Never used by the production Dockerfile."""
import json
from app.adapters import AdapterRegistry
from app.main import create_app


class RoutingFixture:
    async def stream(self, messages, tools, role, **kwargs):
        selected = []
        for message in messages:
            text = message.get('content', '')
            if isinstance(text, str) and 'Selected Odoo context identifiers' in text:
                selected = [r['id'] for r in json.loads(text.split(': ', 1)[1]) if r['model'] == 'hr.applicant']
        request = next((m.get('content', '') for m in reversed(messages) if m.get('role') == 'user' and 'Selected Odoo context' not in str(m.get('content')) and 'registry' not in str(m.get('content'))), '')
        if selected or any(w in request.lower() for w in ('candidate','interview','ứng viên')):
            action = 'schedule' if any(w in request.lower() for w in ('book','schedule','đặt lịch')) else 'report' if any(w in request.lower() for w in ('evaluate','report','đánh giá')) else 'screen'
            yield {'type':'tool.call','id':'synthetic-routing','name':'candidate_workflow','arguments':json.dumps({'action':action,'applicant_id':selected[0] if len(selected)==1 else None})}
        else:
            yield {'type':'text.delta','text':'Synthetic test adapter host. Select a candidate to begin.'}


class FixtureAdapter:
    """Returns deterministic handoffs to test the host, never real candidate judgments."""
    async def invoke(self, assignment, runtime):
        role, case, sources = assignment['role'], assignment['case'], assignment['registry']
        if role == 'Filter':
            # Prefer extracted CV text over attachment metadata so the test
            # contract verifies evidence citations point to the document body.
            cvs = [s for s in sources if s.get('attachment_id') and s.get('locator', '').startswith(('line:', 'page:', 'paragraph:'))]
            findings = []
            for c in case['criteria']:
                keyword = {'C1': 'python', 'C2': 'postgresql', 'C3': 'communication'}.get(c['id'], '')
                source = next((s for s in cvs if keyword and keyword in s['excerpt'].casefold()), None)
                findings.append({'criterion_id':c['id'], 'status':'partial' if source else 'unknown','finding':'Synthetic adapter fixture: candidate self-report requires review.' if source else 'No matching CV excerpt establishes this criterion.', 'citations':[{'source_key':source['key'],'quote':source['excerpt']}] if source else [],'question':'Describe your own contribution to this work.'})
            return {'summary':'Synthetic adapter CV evidence review.','findings':findings}
        if role == 'Schedule':
            return {'start':None,'timezone':None,'duration_minutes':None,'participant_count':None,'participants':None,'candidate_email':None}
        if role == 'Interview' and assignment.get('handoff_name') == 'submit_guide':
            questions = []
            for i in range(5):
                c=case['criteria'][i % len(case['criteria'])]
                questions.append({'criterion_id':c['id'],'question':f"Question {i+1}: Describe your own experience with {c['text'][:180]}.",'follow_up':None})
            return {'language':'en','questions':questions}
        answer_sources = [s for s in sources if s['model']=='jobhunter.chat.answer']
        if answer_sources:
            s=answer_sources[0]
            return {'text':f"Submitted interview evidence (candidate self-report): {s['excerpt']} [{s['key']}]\n\nThis report preserves the submitted evidence; hiring decisions remain with the recruiter."}
        if role == 'Interview':
            return {'text':f"No submitted interview answers are available. [{sources[0]['key']}]"}
        if case.get('schedule') and case['schedule'].get('guide_ready') and not case['schedule'].get('booking'):
            return {'text':f"The interview guide and scheduling draft are prepared. Review the booking preview and confirm before booking or sending. [{sources[-1]['key']}]"}
        if case.get('screening') and assignment.get('mode','').startswith('Summarize'):
            return {'text':f"The saved CV evidence review is available. No submitted interview answers are available; interview evaluation remains incomplete. [{sources[-1]['key']}]"}
        return {'text':f"No submitted interview answers are available; evaluation remains incomplete. [{sources[0]['key']}]"}


def create():
    return create_app(provider=RoutingFixture(), adapters=AdapterRegistry({r:FixtureAdapter() for r in ('Filter','Schedule','Interview','Report')}))
