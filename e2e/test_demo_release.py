"""Real browser checks over the explicitly synthetic deployment dataset.

Provider mode and deployment origin are supplied by the runner. Do not enable
tracing: candidate URLs are bearer credentials. Screenshots omit browser chrome.
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from playwright.sync_api import expect

BASE = os.getenv('JOBHUNTER_PANEL_URL', 'http://localhost:18089').rstrip('/')
OUT = Path(os.getenv('JOBHUNTER_PANEL_ARTIFACTS', '.state/evidence/release-local'))
OUT.mkdir(parents=True, exist_ok=True)
LIVE = os.getenv('JOBHUNTER_RELEASE_LIVE') == '1'


def login(page):
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.goto(BASE + '/odoo/recruitment-applications', wait_until='domcontentloaded', timeout=60000)
    if '/web/login' in page.url:
        page.get_by_role('textbox', name='Email', exact=True).fill(os.getenv('JOBHUNTER_PANEL_LOGIN', 'workflow.qa1'))
        page.get_by_role('textbox', name='Password', exact=True).fill(os.getenv('JOBHUNTER_PANEL_PASSWORD', 'release-fixture-only'))
        page.get_by_role('button', name='Log in', exact=True).click()
    panel = page.get_by_role('region', name='Jobhunter Chat')
    expect(panel).to_be_visible(timeout=60000)
    return panel


def select_candidate(page, panel, name):
    panel.get_by_role('button', name='New chat', exact=True).click()
    panel.get_by_role('button', name='Add Odoo context', exact=True).click()
    picker = panel.locator('.jh-picker')
    picker.get_by_role('button', name='Applicants', exact=True).click()
    picker.get_by_role('textbox', name='Search Odoo records', exact=True).fill(name)
    picker.locator('.jh-picker-select').filter(has_text=name).click()
    workflow = panel.get_by_role('region', name='Candidate workflow')
    expect(workflow.get_by_role('button', name='Review CV', exact=True)).to_be_enabled(timeout=30000)
    return workflow


def complete_turn(panel, previous):
    expect(panel.locator('.jh-answer')).to_have_count(previous + 1, timeout=125000)
    expect(panel.get_by_role('button', name='Send message', exact=True)).to_be_visible(timeout=125000)
    expect(panel.locator('.jh-turn').last.locator('.jh-run-error')).to_have_count(0)


def review_cv(workflow, panel):
    before = panel.locator('.jh-answer').count()
    approve = workflow.get_by_role('button', name='Approve criteria & review CV', exact=True)
    (approve if approve.is_visible() else workflow.get_by_role('button', name='Review CV', exact=True)).click()
    complete_turn(panel, before)
    expect(workflow.locator('.jh-screening-findings li')).to_have_count(5, timeout=30000)
    expect(workflow.locator('.jh-finding-status')).to_have_count(5)


def test_responsive_demo_workspace(page):
    panel = login(page)
    expect(page.get_by_text('[DEMO] Alex - Python Backend Engineer', exact=True)).to_be_visible(timeout=30000)
    expect(page.get_by_text('Matching Score', exact=True)).to_have_count(0)
    expect(page.get_by_text('Matching Factors', exact=True)).to_have_count(0)
    expect(page.get_by_text('Missing Factors', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Collapse AI chat', exact=True).click()
    for width in (390, 768, 1024, 1440):
        page.set_viewport_size({'width': width, 'height': 960})
        expect(page.get_by_role('button', name='New', exact=True)).to_be_visible()
        search = page.locator('.o_searchview')
        if search.is_visible():
            style = search.evaluate('(el) => {const s=getComputedStyle(el); const toggle=el.parentElement.querySelector(".o_searchview_dropdown_toggler"); const outer=toggle?.getClientRects().length ? getComputedStyle(toggle) : s; return {left:s.borderLeftColor,right:outer.borderRightColor,leftWidth:s.borderLeftWidth,rightWidth:outer.borderRightWidth}}')
            if style['rightWidth'] != '0px':
                assert style['left'] == style['right'] and style['leftWidth'] == style['rightWidth'], style
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2'), f'Page overflow at {width}'
        page.screenshot(path=str(OUT / f'workspace-{width}.png'))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.get_by_role('button', name='Toggle AI chat', exact=True).click()
    expect(panel).to_be_visible()


def test_cv_documents_and_direct_interview(browser, page):
    panel = login(page)
    evidence = []
    marker = '[DEMO] verification ' + uuid4().hex
    snapshots = []
    def capture_case(response):
        if response.url.endswith('/jobhunter/chat/rpc'):
            data = response.json().get('result', {})
            if isinstance(data, dict) and isinstance(data.get('case'), dict):
                snapshots.append(data['case'])
    page.on('response', capture_case)
    for name in ('Alex', 'Blair', 'Casey'):
        if name != 'Alex':
            page.goto(BASE + '/odoo/recruitment-applications', wait_until='domcontentloaded', timeout=60000)
            expect(panel).to_be_visible(timeout=60000)
        workflow = select_candidate(page, panel, f'[DEMO] {name} - Python Backend Engineer')
        review_cv(workflow, panel)
        findings = workflow.locator('.jh-finding-status').all_text_contents()
        evidence.append({'candidate': name, 'findings': findings})
        assert all(value in ('supported', 'partial', 'unknown', 'conflicting') for value in findings)
        page.screenshot(path=str(OUT / f'cv-{name.lower()}.png'))
        if name == 'Alex':
            before = panel.locator('.jh-answer').count()
            workflow.get_by_role('button', name='Summarize evidence', exact=True).click()
            complete_turn(panel, before)
            before = panel.locator('.jh-answer').count()
            workflow.get_by_role('button', name='Prepare interview', exact=True).click()
            complete_turn(panel, before)
            workflow.get_by_role('button', name='Create private interview link', exact=True).click()
            link = workflow.get_by_role('link', name='Open typed interview', exact=True)
            expect(link).to_be_visible(timeout=30000)
            target = urljoin(BASE + '/', link.get_attribute('href'))
            assert urlsplit(target).scheme == urlsplit(BASE).scheme and urlsplit(target).netloc == urlsplit(BASE).netloc, 'Interview origin mismatch'
            candidate_context = browser.new_context(viewport={'width': 1024, 'height': 900})
            candidate = candidate_context.new_page()
            try:
                candidate.goto(target)
                for index in range(5):
                    answer = candidate.get_by_role('textbox', name='Your answer / Câu trả lời', exact=True)
                    expect(answer).to_be_visible(timeout=30000)
                    answer.fill(f'{marker} Fictional submitted answer {index + 1}: I built a small Python API project, tested its PostgreSQL queries and documented my own contribution. This is synthetic participation for verification.')
                    candidate.get_by_role('button', name='Submit answer / Gửi câu trả lời', exact=True).click()
                    expect(candidate.locator('#progress')).to_contain_text(f'{index + 1} of 5 answers submitted')
                candidate.get_by_role('button', name='Finish interview / Kết thúc', exact=True).click()
                expect(candidate.locator('#progress')).to_contain_text('completed')
                candidate.screenshot(path=str(OUT / 'interview-completed.png'))
            finally:
                candidate_context.close()
            page.reload()
            expect(panel).to_be_visible(timeout=60000)
            panel.get_by_role('button', name='Open history', exact=True).click()
            panel.locator('.jh-history-open').first.click()
            if not LIVE:
                expect(panel.locator('.jh-answer').last).to_contain_text(marker, timeout=15000)
            expect(panel.get_by_text('Automation: completed', exact=False)).to_be_visible(timeout=125000)
            current = next(case for case in reversed(snapshots) if case.get('applicant_name') == '[DEMO] Alex - Python Backend Engineer')
            answer_ids = {answer['id'] for answer in current['interview']['answers']}
            cited_answer_ids = {source['record_id'] for source in current['report']['sources'] if source['model'] == 'jobhunter.chat.answer'}
            assert len(answer_ids) == 5 and answer_ids <= cited_answer_ids, 'Report did not use the current submitted answers'
            expect(panel.locator('.jh-run-error')).to_have_count(0)
            expect(panel.get_by_text(re.compile('5 submitted answers'))).to_be_visible(timeout=30000)
            page.screenshot(path=str(OUT / 'interview-report.png'))
    (OUT / 'cv-evidence.json').write_text(json.dumps({'provider': 'live' if LIVE else 'synthetic fixture', 'cases': evidence}, indent=2))


def test_full_workflow_requires_exact_booking_review(page):
    panel = login(page)
    candidate_name = os.getenv('JOBHUNTER_BOOKING_DEMO_NAME', '[DEMO] Dana - Flutter Engineer')
    workflow = select_candidate(page, panel, candidate_name)
    review_cv(workflow, panel)
    workflow.get_by_role('button', name='Schedule interview', exact=True).click()
    start = (datetime.now() + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    local_start, local_end = start.isoformat(timespec='minutes'), end.isoformat(timespec='minutes')
    workflow.get_by_role('textbox', name='Interview timezone', exact=True).fill('Asia/Ho_Chi_Minh')
    workflow.get_by_label('Interview start', exact=True).fill(local_start)
    workflow.get_by_label('Duration in minutes', exact=True).fill('30')
    candidate_email = workflow.get_by_label('Participant 1 email', exact=True).input_value()
    workflow.get_by_role('button', name='Add participant', exact=True).click()
    for index, (name, email) in enumerate(((candidate_name, candidate_email), ('Synthetic interviewer', 'workflow.qa1@example.invalid')), 1):
        workflow.get_by_label(f'Participant {index} name', exact=True).fill(name)
        workflow.get_by_label(f'Participant {index} email', exact=True).fill(email)
        workflow.get_by_label(f'Participant {index} timezone', exact=True).fill('Asia/Ho_Chi_Minh')
        workflow.get_by_label(f'Participant {index} available from', exact=True).fill(local_start)
        workflow.get_by_label(f'Participant {index} available until', exact=True).fill(local_end)
    before = panel.locator('.jh-answer').count()
    workflow.get_by_role('button', name='Run full workflow', exact=True).click()
    complete_turn(panel, before)
    expect(workflow.get_by_text('Automation: review', exact=False)).to_be_visible(timeout=30000)
    expect(workflow.get_by_text(re.compile(r'Reservation #\d+'))).to_have_count(0)
    confirmation = workflow.get_by_role('button', name='Confirm booking & send', exact=True)
    expect(confirmation).to_be_visible()
    page.screenshot(path=str(OUT / 'booking-review.png'))
    before = panel.locator('.jh-answer').count()
    confirmation.click()
    if LIVE:
        expect(panel.locator('.jh-turn').last.locator('.jh-run-error')).to_be_visible(timeout=125000)
        expect(workflow.get_by_text(re.compile(r'Reservation #\d+'))).to_have_count(0)
    else:
        complete_turn(panel, before)
        expect(workflow.get_by_text(re.compile(r'Reservation #\d+ .*Invitation: queued'))).to_be_visible(timeout=30000)
        expect(workflow.get_by_text('Automation: awaiting_candidate', exact=False)).to_be_visible()
    page.screenshot(path=str(OUT / 'booking-confirmation.png'))
