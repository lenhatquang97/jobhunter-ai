import importlib.util
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    'rag_agent_under_test', Path(__file__).parents[1] / 'app' / 'agent.py')
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


def test_chunks_keep_long_text_and_sources():
    with patch.object(agent, 'embed', side_effect=lambda texts, model: [[1.0]] * len(texts)):
        items = agent.build_index({'company': 'Company context ' * 100, 'cv': 'CV claim'}, 'embed')
    assert items and {x['source'] for x in items} == {'company', 'cv'}


def test_retrieve_keeps_each_source():
    index = [
        {'source': 'company', 'text': 'c', 'vec': [1.0, 0.0]},
        {'source': 'job', 'text': 'j', 'vec': [0.99, 0.01]},
        {'source': 'cv', 'text': 'v', 'vec': [0.0, 1.0]},
    ]
    got = agent.retrieve(index, [1.0, 0.0], k=1)
    assert {x['source'] for x in got} == {'company', 'job', 'cv'}


CFG = {'turn_model': 'turn', 'embed_model': 'embed'}
INDEX = [{'source': 'job', 'text': 'JD', 'vec': [1.0, 0.0]}]


def _turn(reply, history, asked, max_topics=3, max_probes=2):
    with patch.object(agent, 'embed', return_value=[[1.0, 0.0]]), patch.object(agent, 'chat', return_value=reply):
        return agent.next_turn(CFG, {'job': 'Dev', 'company': 'Acme', 'max_topics': max_topics,
                                     'max_probes': max_probes}, INDEX, history, asked, 'answer')


def test_probe_stays_on_topic_and_does_not_use_topic_budget():
    reply = {'evidence': 'e', 'action': 'probe', 'topic': 'x', 'next_question': '**deeper?**', 'flags': []}
    got = _turn(reply, [], {'question': 'q1', 'topic': 'Redis', 'topic_no': 1})
    assert (got['action'], got['topic'], got['topic_no'], got['done']) == ('probe', 'Redis', 1, False)
    assert got['question'] == 'deeper?'


def test_probe_budget_forces_new_topic():
    history = [{'question': 'q', 'answer': 'a', 'topic': 'Redis', 'topic_no': 1}] * 2
    reply = {'evidence': 'e', 'action': 'probe', 'topic': 'PostgreSQL', 'next_question': 'q?', 'flags': []}
    got = _turn(reply, history, {'question': 'q3', 'topic': 'Redis', 'topic_no': 1})
    assert (got['action'], got['topic'], got['topic_no']) == ('new_topic', 'PostgreSQL', 2)


def test_topic_budget_forces_end():
    history = [{'question': 'q', 'answer': 'a', 'topic': 't', 'topic_no': n} for n in (1, 2)]
    reply = {'evidence': 'e', 'action': 'new_topic', 'topic': 'y', 'next_question': 'q?', 'flags': []}
    got = _turn(reply, history, {'question': 'q', 'topic': 't', 'topic_no': 3}, max_probes=0)
    assert got['done'] is True and got['action'] == 'end'


def test_realtime_secret_never_exposes_api_key():
    with patch.object(agent, '_post', return_value={'value': 'ephemeral'} ) as post:
        assert agent.realtime_secret('gpt-realtime', 'marin', 'gpt-4o-transcribe') == 'ephemeral'
        assert post.call_args.args[2] == 'OPENAI_API_KEY'


def test_report_keeps_only_submitted_answer_evidence_and_fills_unknown_criteria():
    model = {
        'summary': 'Candidate described recent work.',
        'strengths': [],
        'gaps': [],
        'cv_claims': ['The candidate self-reported Python work.'],
        'findings': [
            {'criterion_id': 'current-role', 'status': 'supported', 'statement': 'Role described.',
             'evidence_turn_ids': ['answer-1']},
            {'criterion_id': 'motivation', 'status': 'supported', 'statement': 'No valid evidence.',
             'evidence_turn_ids': ['made-up']},
        ],
    }
    transcript = [{'id': 'answer-1', 'question': 'What do you do?', 'answer': 'I build APIs.'}]
    with patch.object(agent, 'chat', return_value=model):
        result = agent.report('report', {'job': 'Dev', 'company': 'Acme'}, {'cv': 'CV'}, transcript, '')
    findings = {item['criterion_id']: item for item in result['findings']}
    assert findings['current-role']['evidence_turn_ids'] == ['answer-1']
    assert findings['motivation']['status'] == 'unknown'
    assert findings['availability']['status'] == 'unknown'


def test_screening_prompt_does_not_infer_scripted_or_ai_generated_answers():
    prompt = agent.turn_system('Dev', 'Acme', 'state')
    assert 'scripted_answer' not in prompt
    assert 'Do not infer whether an answer was written by AI' in prompt
