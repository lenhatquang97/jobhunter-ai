"""Browser verification against the disposable Odoo/PostgreSQL/Mailpit stack.

Run with `uv run pytest e2e/test_candidate_workflows.py --tracing retain-on-failure
--screenshot only-on-failure --junitxml=e2e/test-results/pytest.xml`.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import Browser, Page, expect


ODOO = os.getenv("JOBHUNTER_E2E_BASE_URL", "http://localhost:18069").rstrip("/")
MAILPIT = os.getenv("JOBHUNTER_E2E_MAILPIT_URL", "http://localhost:18025").rstrip("/")
LOGIN = os.getenv("JOBHUNTER_E2E_LOGIN", "workflow.qa1")
PASSWORD = os.getenv("JOBHUNTER_E2E_PASSWORD", "workflow-test-only")
CANDIDATE = "E2E Alex Software Engineer"
CANDIDATE_EMAIL = "netlink269@gmail.com"


def json_get(url: str, timeout: float = 3) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def mailpit_messages() -> dict:
    return json_get(f"{MAILPIT}/api/v1/messages")


def open_chat(page: Page) -> Page:
    page.set_default_timeout(15_000)
    page.goto(f"{ODOO}/odoo/action-271?debug=assets", wait_until="domcontentloaded")
    email = page.get_by_role("textbox", name=re.compile("Email"))
    if "/web/login" in page.url:
        email.wait_for(state="visible", timeout=15_000)
        email.fill(LOGIN)
        page.get_by_role("textbox", name="Password").fill(PASSWORD)
        page.get_by_role("button", name="Log in", exact=True).click()
        # The protected action remains in Odoo's login redirect, so a successful
        # login returns directly to it without an extra navigation losing session.
    expect(page.get_by_role("region", name="Jobhunter Chat")).to_be_visible(timeout=45_000)
    expect(page.get_by_role("textbox", name="Message Jobhunter")).to_be_visible()
    return page


def add_applicant(page: Page, name: str) -> None:
    page.get_by_role("button", name="Add Odoo context").click()
    dialog = page.get_by_role("dialog", name="Add Odoo context")
    dialog.get_by_role("button", name="Applicants").click()
    search = dialog.get_by_role("textbox", name="Search Odoo records")
    search.fill(name)
    row = dialog.locator(".jh-picker-record").filter(has_text=name)
    expect(row).to_be_visible()
    row.locator(".jh-picker-select").click()
    expect(dialog).to_have_count(0)


def expand_latest_activity(page: Page) -> None:
    toggle = page.get_by_role("button", name=re.compile(r"Worked for")).last
    toggle.click()


def wait_for_captured_invitation(previous_count: int, timeout: float = 90) -> dict:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            messages = mailpit_messages()
            if messages.get("messages_count", 0) >= previous_count + 1:
                matches = [
                    message
                    for message in messages.get("messages", [])
                    if any(
                        recipient.get("Address", "").casefold() == CANDIDATE_EMAIL.casefold()
                        for recipient in message.get("To", [])
                    )
                ]
                if matches:
                    return matches[0]
        except (OSError, URLError, TimeoutError) as error:
            last_error = error
        time.sleep(0.5)
    raise AssertionError(f"No captured invitation reached {CANDIDATE_EMAIL}; last error={last_error!r}")


def test_candidate_workflow_screen_schedule_interview_report_and_reuse(browser: Browser, page: Page):
    """Exercise the recruiter and candidate pages across three private chats."""
    open_chat(page)
    initial_mail_count = mailpit_messages().get("messages_count", 0)

    add_applicant(page, CANDIDATE)
    workflow = page.get_by_role("region", name="Candidate workflow")
    expect(workflow.get_by_role("combobox", name="Focused candidate")).to_have_value(re.compile(r"\d+"))
    expect(workflow).to_contain_text("Python service development")
    expect(workflow).to_contain_text("PostgreSQL database experience")
    expect(workflow).to_contain_text("Clear written communication")

    if page.get_by_role("button", name="Approve criteria & review CV").count():
        page.get_by_role("button", name="Approve criteria & review CV").click()
    else:
        workflow.get_by_role("button", name="Review CV").click()
    expect(workflow).to_contain_text("CV review: current", timeout=60_000)
    expect(workflow.get_by_text("CV evidence by criterion")).to_be_visible()
    expect(workflow).to_contain_text("Python service development")
    expect(workflow).to_contain_text("partial")
    expect(workflow).to_contain_text("unknown")
    expect(workflow).to_contain_text("Built Python services backed by PostgreSQL.")
    expect(workflow.get_by_role("button", name=re.compile(r"S4 .*line:1"))).to_be_visible()
    expect(page.locator(".jh-answer").last).to_contain_text("saved CV evidence review")
    expand_latest_activity(page)
    filter_activity = page.locator(".jh-agent").all_inner_texts()
    assert any("Filter" in agent for agent in filter_activity), filter_activity
    assert any("Report" in agent for agent in filter_activity), filter_activity
    filter_tools = page.locator(".jh-tool").all_inner_texts()
    assert any("read_document" in tool for tool in filter_tools), filter_tools
    assert any("workflow_save" in tool for tool in filter_tools), filter_tools
    assert mailpit_messages().get("messages_count", 0) == initial_mail_count

    # A second chat reuses the recruiter-owned candidate case and first exercises
    # the missing-input handoff without creating a reservation or an invitation.
    page.get_by_role("button", name="New chat").first.click()
    add_applicant(page, CANDIDATE)
    workflow = page.get_by_role("region", name="Candidate workflow")
    workflow.get_by_role("button", name="Schedule interview").click()
    expect(page.get_by_role("group", name="Interview scheduling details")).to_be_visible()
    page.get_by_role("button", name="Prepare booking preview").click()
    expect(page.get_by_text(re.compile("Please provide or correct:"))).to_be_visible(timeout=60_000)
    expect(page.locator(".jh-booking-preview")).to_contain_text("needs_input")
    expect(page.get_by_role("button", name="Confirm booking & send")).to_have_count(0)
    assert mailpit_messages().get("messages_count", 0) == initial_mail_count

    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    requested = (datetime.now(zone) + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
    available_from = requested - timedelta(minutes=30)
    available_until = requested + timedelta(minutes=60)
    local = lambda value: value.strftime("%Y-%m-%dT%H:%M")
    page.get_by_role("textbox", name="Interview start").fill(local(requested))
    page.get_by_role("textbox", name="Interview timezone").fill("Asia/Ho_Chi_Minh")
    page.get_by_role("spinbutton", name="Duration in minutes").fill("30")
    page.get_by_role("textbox", name="Participant 1 timezone").fill("Asia/Ho_Chi_Minh")
    page.get_by_role("textbox", name="Participant 1 available from").fill(local(available_from))
    page.get_by_role("textbox", name="Participant 1 available until").fill(local(available_until))
    page.get_by_role("button", name="Add participant").click()
    page.get_by_role("textbox", name="Participant 2 name").fill("E2E Recruiter")
    page.get_by_role("textbox", name="Participant 2 email").fill("recruiter@example.invalid")
    page.get_by_role("textbox", name="Participant 2 timezone").fill("Asia/Ho_Chi_Minh")
    page.get_by_role("textbox", name="Participant 2 available from").fill(local(available_from))
    page.get_by_role("textbox", name="Participant 2 available until").fill(local(available_until))
    page.get_by_role("button", name="Prepare booking preview").click()

    expect(page.get_by_text("The interview guide and scheduling draft are prepared.")).to_be_visible(timeout=60_000)
    preview = page.get_by_role("region", name="Booking preview")
    expect(preview).to_contain_text("Interview booking: ready")
    expect(preview).to_contain_text("UTC:")
    expect(preview).to_contain_text(CANDIDATE_EMAIL)
    expect(page.get_by_role("button", name="Confirm booking & send")).to_be_visible()
    assert mailpit_messages().get("messages_count", 0) == initial_mail_count
    expand_latest_activity(page)
    schedule_activity = page.locator(".jh-agent").all_inner_texts()
    assert any("Schedule" in agent for agent in schedule_activity), schedule_activity
    assert any("Interview" in agent for agent in schedule_activity), schedule_activity
    schedule_tools = page.locator(".jh-tool").all_inner_texts()
    assert any("workflow_schedule" in tool for tool in schedule_tools), schedule_tools
    assert any("workflow_prepare" in tool for tool in schedule_tools), schedule_tools

    page.get_by_role("button", name="Confirm booking & send").click()
    expect(page.get_by_text(re.compile(r"Reservation #\d+"))).to_be_visible(timeout=60_000)
    expect(page.locator(".jh-answer").last).to_contain_text("booking step completed")
    invitation = wait_for_captured_invitation(initial_mail_count)
    detail = json_get(f"{MAILPIT}/api/v1/message/{invitation['ID']}")
    assert CANDIDATE_EMAIL in json.dumps(detail)
    body = "\n".join(str(detail.get(key, "")) for key in ("HTML", "Text", "Body", "Snippet"))
    link_match = re.search(r"(?:https?://[^\s\"'<>]+)?/jobhunter/interview/[A-Za-z0-9_-]{40,100}", body)
    assert link_match, f"Captured invitation did not include a private interview link: {body[:1000]}"
    invite_url = urljoin(ODOO, link_match.group(0))

    # The public candidate session is tested in a fresh browser context with no
    # recruiter cookies. A reload must resume at the next unanswered question.
    candidate_context = browser.new_context()
    try:
        candidate_page = candidate_context.new_page()
        candidate_page.goto(invite_url, wait_until="domcontentloaded")
        expect(candidate_page.get_by_role("heading", name="Your interview")).to_be_visible()
        progress = candidate_page.get_by_role("status")
        expect(progress).to_contain_text("0 of 5 answers submitted")
        question = candidate_page.get_by_role("heading", level=2)
        expect(question).to_contain_text("Question 1")
        first_answer = "E2E answer 1: candidate self-report about Python services."
        candidate_page.get_by_role("textbox", name="Your answer / Câu trả lời").fill(first_answer)
        candidate_page.get_by_role("button", name="Submit answer / Gửi câu trả lời").click()
        expect(progress).to_contain_text("1 of 5 answers submitted")
        candidate_page.reload(wait_until="domcontentloaded")
        progress = candidate_page.get_by_role("status")
        expect(progress).to_contain_text("1 of 5 answers submitted")
        expect(candidate_page.get_by_text(first_answer)).to_be_visible()
        for number in range(2, 6):
            expect(candidate_page.get_by_role("heading", level=2)).to_contain_text(f"Question {number}")
            candidate_page.get_by_role("textbox", name="Your answer / Câu trả lời").fill(
                f"E2E answer {number}: candidate self-report from the synthetic interview."
            )
            candidate_page.get_by_role("button", name="Submit answer / Gửi câu trả lời").click()
            expect(progress).to_contain_text(f"{number} of 5 answers submitted")
        candidate_page.get_by_role("button", name="Finish interview / Kết thúc").click()
        expect(progress).to_contain_text("completed")
    finally:
        candidate_context.close()

    # A third conversation reuses all current Alex artifacts and actually runs
    # Interview and Report after the five explicit candidate submissions.
    page.get_by_role("button", name="New chat").first.click()
    add_applicant(page, CANDIDATE)
    workflow = page.get_by_role("region", name="Candidate workflow")
    expect(workflow).to_contain_text("CV review: current")
    expect(workflow).to_contain_text("Interview: completed · 5 submitted answers")
    workflow.get_by_role("button", name="Evaluate candidate").click()
    expect(page.locator(".jh-answer").last).to_contain_text(first_answer, timeout=60_000)
    expect(page.locator(".jh-answer").last).to_contain_text("candidate self-report")
    expand_latest_activity(page)
    report_activity = page.locator(".jh-agent").all_inner_texts()
    assert any("Interview" in agent for agent in report_activity), report_activity
    assert any("Report" in agent for agent in report_activity), report_activity
    assert len(page.locator(".jh-history-row").all()) >= 3


def test_two_candidates_can_be_focused_in_one_chat_without_artifact_leakage(page: Page):
    open_chat(page)
    add_applicant(page, CANDIDATE)
    add_applicant(page, "E2E Blake Software Engineer")
    workflow = page.get_by_role("region", name="Candidate workflow")
    selector = workflow.get_by_role("combobox", name="Focused candidate")
    options = selector.locator("option").all_text_contents()
    assert CANDIDATE in options and "E2E Blake Software Engineer" in options, options

    selector.select_option(label="E2E Blake Software Engineer")
    expect(workflow.get_by_text("Loading candidate workflow…")).to_have_count(0)
    expect(workflow.locator(".jh-workflow-status")).to_have_count(0)
    expect(workflow).to_contain_text("E2E Blake Software Engineer")
    expect(workflow).not_to_contain_text(CANDIDATE_EMAIL)

    selector.select_option(label=CANDIDATE)
    expect(workflow).to_contain_text("CV review: current")
    expect(workflow).to_contain_text("Interview: completed · 5 submitted answers")
    expect(workflow).not_to_contain_text("E2E Blake Software Engineer CV.txt")
