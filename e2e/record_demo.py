"""Record the verified synthetic panel flow, using private cloud credentials.

Run from repository root: uv run python e2e/record_demo.py
The ignored cloud handoff is never copied into artifacts. Viewport recordings
exclude browser URL bars, and no trace/network capture is written.
"""

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    private = json.loads(Path(".state/cloud-private.json").read_text())
    # The deployment handoff supplies these dedicated synthetic test identities.
    os.environ["JOBHUNTER_PANEL_URL"] = private["public_url"]
    os.environ["JOBHUNTER_PANEL_LOGIN"] = private["recruiter_login"]
    os.environ["JOBHUNTER_PANEL_PASSWORD"] = private["recruiter_password"]
    os.environ["JOBHUNTER_PANEL_VIDEO"] = "1"
    os.environ["JOBHUNTER_PANEL_PROVIDER_MODE"] = "live-openai-deployed"
    os.environ.setdefault("JOBHUNTER_PANEL_ARTIFACTS", ".state/evidence/deployed")
    import test_panel

    artifacts = test_panel.ARTIFACTS
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 960},
            record_video_size={"width": 1440, "height": 960},
            record_video_dir=str(artifacts / "recruiter-video"),
        )
        started = time.monotonic()
        page = context.new_page()
        try:
            test_panel.test_filter_direct_typed_interview_and_summary(browser, page)
            # Give the completed evidence view a readable hold in the recording.
            page.wait_for_timeout(2500)
        finally:
            context.close()
            browser.close()
        timeline = [
            dict(item, time=round(item["time"] - started, 3)) for item in test_panel.VIDEO_TIMELINE
        ]
        (artifacts / "video-timeline.json").write_text(json.dumps(timeline, indent=2) + "\n")
    print("Deployed synthetic Filter and typed Interview flow passed; viewport videos saved.")


if __name__ == "__main__":
    main()
