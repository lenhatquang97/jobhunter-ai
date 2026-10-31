import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location('rag_matcher_under_test', Path(__file__).parents[1] / 'models' / 'matcher.py')
matcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(matcher)
sys.path.insert(0, str(Path(__file__).parents[1]))
from rag_filter_application.dto.requests import CandidateDocumentRequest, ScreeningRequest
from rag_filter_application.services.screening_service import ScreeningService

REQS = {'job': 'Dev', 'requirements': [{'name': 'Java', 'priority': 'must'}, {'name': 'Docker', 'priority': 'nice'}]}

def test_score_weights_must_and_partial():
    reqs = REQS['requirements']
    assert matcher.score(reqs, [{'no': 1, 'status': 'matched'}, {'no': 2, 'status': 'missing'}]) == 75
    assert matcher.score(reqs, [{'no': 1, 'status': 'partial'}, {'no': 2, 'status': 'matched'}]) == 62
    assert matcher.score(reqs, []) == 0  # skipped requirements count as missing

def test_rank_sorts_best_first_and_survives_bad_cv():
    def fake_chat(model, system, parts, schema):
        if schema is matcher.REQ_SCHEMA:
            return REQS
        name = parts[1]['file']['filename']
        if name == 'bad.pdf':
            raise matcher.MatchError('boom')
        status = 'matched' if name == 'good.pdf' else 'missing'
        return {'candidate': name, 'summary': '', 'verdicts': [{'no': 1, 'status': status, 'evidence': 'e'}]}

    with patch.object(matcher, 'chat', side_effect=fake_chat):
        _, got = matcher.rank('m', ('jd.pdf', b'%PDF'), [('weak.pdf', b'x'), ('bad.pdf', b'x'), ('good.pdf', b'x')])
    assert [r['file'] for r in got][0] == 'good.pdf' and got[0]['score'] == 75
    assert next(r for r in got if r['file'] == 'bad.pdf')['error'] == 'boom'
    assert [i['status'] for i in got[0]['items']] == ['matched', 'missing']

def test_standalone_matcher_accepts_injected_client():
    calls = []

    def fake_chat(model, system, parts, schema):
        calls.append((model, schema))
        if schema is matcher.REQ_SCHEMA:
            return REQS
        return {'candidate': 'A', 'summary': 'ok', 'verdicts': [
            {'no': 1, 'status': 'matched', 'evidence': 'Java'},
            {'no': 2, 'status': 'matched', 'evidence': 'Docker'},
        ]}

    client = matcher.Matcher('test-model', chat_fn=fake_chat)
    reqs, got = client.rank(('jd.pdf', b'%PDF'), [('a.pdf', b'%PDF')])
    assert reqs == REQS
    assert got[0]['score'] == 100
    assert [schema for _, schema in calls] == [matcher.REQ_SCHEMA, matcher.EVAL_SCHEMA]

def test_score_ignores_duplicate_and_out_of_range_verdicts():
    reqs = REQS['requirements']
    assert matcher.score(reqs, [
        {'no': 1, 'status': 'matched'}, {'no': 1, 'status': 'missing'},
        {'no': 99, 'status': 'matched'},
    ]) == 75

def test_protected_requirements_are_not_scored():
    def fake_chat(model, system, parts, schema):
        assert 'protected' in system.lower()
        return {'job': 'Dev', 'requirements': [
            {'name': 'Java', 'priority': 'must'}, {'name': 'age under 30', 'priority': 'must'}
        ]}

    with patch.object(matcher, 'chat', side_effect=fake_chat):
        got = matcher.requirements('m', 'jd.pdf', b'%PDF')
    assert got['requirements'] == [{'name': 'Java', 'priority': 'must'}]

def test_nlayer_request_response_contract():
    def fake_rank(model, jd, cvs):
        return REQS, [{
            'file': cvs[0][0], 'candidate': 'A', 'score': 75, 'summary': 'ok',
            'items': [{'requirement': 'Java', 'priority': 'must', 'status': 'matched', 'evidence': 'Java'}],
            'error': '',
        }]

    request = ScreeningRequest(
        jd_filename='jd.pdf', jd_content=b'%PDF',
        candidates=(CandidateDocumentRequest('cv.pdf', b'%PDF'),), model='test-model',
    )
    response = ScreeningService(fake_rank).execute(request)
    assert response.job == 'Dev'
    assert response.results[0].filename == 'cv.pdf'
    assert response.results[0].score == 75
    assert response.to_dict()['requirements'][0]['priority'] == 'must'
