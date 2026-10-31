"""Real Odoo + specialist happy path. Provider mode is recorded separately by the runner."""

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import expect

BASE = os.getenv("JOBHUNTER_PANEL_URL", "http://localhost:28069")
ARTIFACTS = Path(os.getenv("JOBHUNTER_PANEL_ARTIFACTS", ".state/evidence/panel"))
ARTIFACTS.mkdir(parents=True, exist_ok=True)
VIDEO_TIMELINE = []


def video_checkpoint(name):
    if os.getenv("JOBHUNTER_PANEL_VIDEO"):
        VIDEO_TIMELINE.append({"name": name, "time": time.monotonic()})


def login(page):
    page.set_viewport_size({"width": 1440, "height": 960})
    page.goto(BASE + os.getenv("JOBHUNTER_PANEL_PATH", "/odoo/recruitment-applications"))
    if "/web/login" in page.url:
        page.get_by_role("textbox", name="Email", exact=True).fill(
            os.getenv("JOBHUNTER_PANEL_LOGIN", "workflow.qa1")
        )
        page.get_by_role("textbox", name="Password", exact=True).fill(
            os.getenv("JOBHUNTER_PANEL_PASSWORD", "workflow-test-only")
        )
        page.get_by_role("button", name="Log in", exact=True).click()
    expect(page.get_by_role("region", name="Jobhunter Chat")).to_be_visible(timeout=45000)
    window = page.get_by_role("button", name="Close Chat Window (ESC)", exact=True)
    if window.is_visible():
        window.click()
    return page.get_by_role("region", name="Jobhunter Chat")


def wait_answer(panel, number):
    expect(
        panel.locator(".jh-turn").nth(number - 1).locator(".jh-answer, .jh-run-error")
    ).to_be_visible(timeout=125000)
    expect(panel.locator(".jh-answer")).to_have_count(number)
    expect(panel.get_by_role("button", name="Send message", exact=True)).to_be_visible(
        timeout=125000
    )
    expect(panel.locator(".jh-run-error")).to_have_count(0)


def test_filter_direct_typed_interview_and_summary(browser, page):
    video_checkpoint("recruiter_start")
    panel = login(page)
    panel.get_by_role("button", name="New chat", exact=True).click()
    page.get_by_text("E2E Alex Software Engineer", exact=True).first.click()
    expect(
        page.get_by_role("heading", name="E2E Alex Software Engineer", exact=True)
    ).to_be_visible()
    panel.get_by_role("button", name="Use in chat", exact=True).click()
    workflow = panel.get_by_role("region", name="Candidate workflow")
    expect(workflow.get_by_role("button", name="Review CV", exact=True)).to_be_enabled()
    approve = workflow.get_by_role("button", name="Approve criteria & review CV", exact=True)
    if approve.is_visible():
        # Regresses the observed overlapping empty-chat content bug.
        assert approve.evaluate(
            "el => { const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); }"
        )
        approve.click()
    else:
        panel.get_by_role("textbox", name="Message Jobhunter").fill(
            "Use the Filter agent to review the selected applicant CV against the approved role criteria."
        )
        panel.get_by_role("button", name="Send message", exact=True).click()
    wait_answer(panel, 1)
    expect(workflow.locator(".jh-screening-findings")).to_contain_text("Python service development")
    expect(workflow.locator(".jh-screening-findings q").first).to_be_visible()
    panel.get_by_role("button", name=re.compile("Worked for")).last.click()
    activity = panel.locator(".jh-activity-body").last
    expect(activity).to_contain_text("Orchestrator")
    expect(activity).to_contain_text("Filter")
    expect(activity).to_contain_text("Report")
    page.screenshot(path=str(ARTIFACTS / "filter.png"))
    workflow.get_by_role("button", name="Prepare interview", exact=True).click()
    wait_answer(panel, 2)
    guide = workflow.locator(".jh-direct-guide")
    expect(guide.locator("li")).to_have_count(5)
    workflow.get_by_role("button", name="Create private interview link", exact=True).click()
    link = workflow.get_by_role("link", name="Open typed interview", exact=True)
    expect(link).to_be_visible(timeout=20000)
    url = link.get_attribute("href")
    # Verify user can collapse, resume, resize, and retain this exact conversation.
    page.get_by_role("button", name="Collapse AI chat", exact=True).click()
    expect(panel).not_to_be_visible()
    page.get_by_role("button", name="Toggle AI chat", exact=True).click()
    expect(link).to_be_visible()
    separator = page.get_by_role("separator", name="Resize AI chat")
    separator.focus()
    separator.press("ArrowLeft")
    expect(separator).to_have_attribute("aria-valuenow", "440")
    separator.press("Home")
    expect(separator).to_have_attribute("aria-valuenow", "420")
    page.screenshot(path=str(ARTIFACTS / "split-overview.png"))
    # A persisted nonsecret UUID permits authorized recovery after a full reload.
    page.reload()
    expect(panel.get_by_role("button", name="Open history", exact=True)).to_be_visible()
    panel.get_by_role("button", name="Open history", exact=True).click()
    panel.locator(".jh-history-open").first.click()
    expect(panel.locator(".jh-answer")).to_have_count(2, timeout=30000)
    panel.get_by_role("button", name="Close history", exact=True).click()
    workflow.get_by_role("button", name="Recover private interview link", exact=True).click()
    expect(link).to_have_attribute("href", url)

    video_checkpoint("candidate_start")
    candidate_options = {"viewport": {"width": 1000, "height": 850}}
    if os.getenv("JOBHUNTER_PANEL_VIDEO"):
        candidate_options.update(
            record_video_dir=str(ARTIFACTS / "candidate-video"),
            record_video_size={"width": 1000, "height": 850},
        )
    candidate_context = browser.new_context(**candidate_options)
    candidate = candidate_context.new_page()
    try:
        candidate.goto(BASE + url)
        expect(candidate.get_by_role("heading", name="Your interview", exact=True)).to_be_visible()
        expect(candidate.locator("#progress")).to_contain_text("0 of 5 answers submitted")
        for i in range(5):
            answer = candidate.get_by_role("textbox", name="Your answer / Câu trả lời", exact=True)
            expect(answer).to_be_visible()
            answer.fill(
                f"Synthetic answer {i + 1}: I built Python services with PostgreSQL and explained tradeoffs to my team."
            )
            if i == 0:
                candidate.screenshot(path=str(ARTIFACTS / "candidate-typed.png"))
            candidate.get_by_role(
                "button", name="Submit answer / Gửi câu trả lời", exact=True
            ).click()
            expect(
                candidate.get_by_text(f"{i + 1} of 5 answers submitted", exact=False)
            ).to_be_visible(timeout=15000)
        candidate.get_by_role("button", name="Finish interview / Kết thúc", exact=True).click()
        expect(candidate.locator("#progress")).to_contain_text("completed")
    finally:
        candidate_context.close()
    video_checkpoint("candidate_end")
    workflow.get_by_role("button", name="Summarize evidence", exact=True).click()
    wait_answer(panel, 3)
    panel.get_by_role("button", name=re.compile("Worked for")).last.click()
    expect(panel.locator(".jh-activity-body").last).to_contain_text("Interview")
    expect(panel.locator(".jh-answer").last).to_contain_text(
        re.compile("self.report|submitted|candidate", re.I)
    )
    page.screenshot(path=str(ARTIFACTS / "final-summary.png"))
    video_checkpoint("recruiter_end")
    evidence = {
        "provider_mode": os.getenv("JOBHUNTER_PANEL_PROVIDER_MODE", "deterministic"),
        "filter": True,
        "interview_guide_questions": 5,
        "submitted_answers": 5,
        "summary": True,
    }
    (ARTIFACTS / "happy-path.json").write_text(json.dumps(evidence, indent=2) + "\n")


def test_panel_preserves_draft_and_candidate_focus_across_native_navigation(page):
    panel = login(page)
    panel.get_by_role("button", name="New chat", exact=True).click()
    composer = panel.get_by_role("textbox", name="Message Jobhunter")
    composer.fill("Unsent recruiting question")
    page.get_by_text("E2E Blake Software Engineer", exact=True).first.click()
    expect(
        page.get_by_role("heading", name="E2E Blake Software Engineer", exact=True)
    ).to_be_visible()
    expect(panel.get_by_role("region", name="Candidate workflow")).to_have_count(0)
    panel.get_by_role("button", name="Use in chat", exact=True).click()
    focus = panel.get_by_role("combobox", name="Focused candidate")
    expect(focus).not_to_have_value("")
    selected = focus.input_value()
    page.get_by_role("button", name="Next", exact=True).click()
    expect(
        page.get_by_role("heading", name="E2E Alex Software Engineer", exact=True)
    ).to_be_visible()
    expect(focus).to_have_value(selected)
    expect(composer).to_have_value("Unsent recruiting question")
    page.get_by_role("button", name="Collapse AI chat", exact=True).click()
    expect(panel).not_to_be_visible()
    page.get_by_role("button", name="Toggle AI chat", exact=True).click()
    expect(composer).to_have_value("Unsent recruiting question")
    separator = page.get_by_role("separator", name="Resize AI chat")
    separator.focus()
    for _ in range(15):
        separator.press("ArrowLeft")
    expect(separator).to_have_attribute("aria-valuenow", "640")
    for _ in range(15):
        separator.press("ArrowRight")
    expect(separator).to_have_attribute("aria-valuenow", "360")
    page.set_viewport_size({"width": 800, "height": 900})
    expect(panel).to_be_visible()
    assert (
        page.locator(".o_action_manager").evaluate("el => getComputedStyle(el).marginRight")
        == "0px"
    )
    assert page.locator(".jh-recruitment-panel").bounding_box()["width"] <= 480
    page.screenshot(path=str(ARTIFACTS / "narrow-panel.png"))
