from datetime import datetime

from .domain import ZONE

COPY = {
    "en": {
        "draft": "Review before sending",
        "cancel_draft": "Review cancellation",
        "proposal": "HR is asking you to confirm this proposed interview time.",
        "pending_notice": "Confirmation requested from the assigned tech lead.",
        "confirmation_followup": "This proposed time is still awaiting the tech lead's response.",
        "confirmed_notice": "The tech lead confirmed this time. HR handles candidate confirmation.",
        "reminder_24h": "Upcoming interview reminder",
        "reminder_30m": "Upcoming interview reminder",
        "change_notice": "The tech lead requested a change. HR must review and send a revised time.",
        "cancelled_notice": "HR cancelled this interview request. Reminders have stopped.",
        "expired_notice": "The proposed start has arrived without confirmation. HR action is needed.",
        "feedback_request": "The scheduled end has passed. Please submit feedback in your existing system, then mark it submitted here. If the interview did not happen, tell HR below.",
        "feedback_followup": "Feedback submission is still outstanding. Please update its status.",
        "feedback_submitted_notice": "The tech lead reported that feedback was submitted in the existing system.",
        "not_happened_notice": "The tech lead reported that the interview did not happen. Feedback reminders have stopped.",
        "clarify": "Please provide a candidate reference, an explicit date and start/end (or duration), and @mention the configured tech lead. Past times and invalid links cannot be sent.",
        "choose_lead": "Please @mention a configured tech lead or give their exact Slack member ID.",
        "choose_interview": "Please reply in the interview's thread, or include its full request ID.",
        "provider_error": "I could not interpret that message. Nothing was sent or changed. Please try again; existing reminder jobs are unaffected.",
        "unavailable": "That request or action is unavailable to you.",
        "stale": "This button is no longer current. Ask for status to get the latest card.",
        "discarded": "Draft discarded. No request was sent.",
        "edit_help": "Reply in this thread with corrected details. I will show a new review card before sending.",
        "withdrawn": "HR withdrew your previous interview request. Its old buttons and reminders are no longer active.",
        "help": "HR: send a candidate reference, date/time, duration and @tech-lead to prepare a request. Reply in its thread to revise it. Ask for status to see your requests. Tech leads: use the confirmation and feedback-status buttons. Please submit feedback in your existing system; do not send feedback text here.",
        "info": "Ask for help or status.",
        "send": "Send request",
        "send_cancel": "Cancel interview",
        "draft_edit": "Edit",
        "discard": "Discard",
        "confirm": "Confirm",
        "change": "Request change",
        "edit": "Change time",
        "cancel_review": "Cancel",
        "feedback": "Feedback submitted",
        "not_happened": "Interview did not happen",
        "state": "Status",
        "ref": "Candidate reference",
        "lead": "Tech lead",
        "request": "Request",
        "issues": "Delivery needs attention",
    },
    "vi": {
        "draft": "Kiểm tra trước khi gửi",
        "cancel_draft": "Kiểm tra yêu cầu hủy",
        "proposal": "HR cần bạn xác nhận thời gian phỏng vấn được đề xuất.",
        "pending_notice": "Đã tạo yêu cầu xác nhận cho tech lead phụ trách.",
        "confirmation_followup": "Thời gian đề xuất vẫn đang chờ tech lead phản hồi.",
        "confirmed_notice": "Tech lead đã xác nhận thời gian. HR phụ trách xác nhận với ứng viên.",
        "reminder_24h": "Nhắc lịch phỏng vấn sắp tới",
        "reminder_30m": "Nhắc lịch phỏng vấn sắp tới",
        "change_notice": "Tech lead yêu cầu đổi lịch. HR cần kiểm tra và gửi thời gian mới.",
        "cancelled_notice": "HR đã hủy yêu cầu phỏng vấn. Đã dừng nhắc lịch.",
        "expired_notice": "Đã đến giờ đề xuất nhưng chưa được xác nhận. HR cần xử lý.",
        "feedback_request": "Đã qua giờ kết thúc dự kiến. Vui lòng gửi nhận xét trong hệ thống hiện tại, sau đó đánh dấu đã gửi ở đây. Nếu buổi phỏng vấn không diễn ra, hãy báo HR bằng nút bên dưới.",
        "feedback_followup": "Chưa có xác nhận đã gửi nhận xét. Vui lòng cập nhật trạng thái.",
        "feedback_submitted_notice": "Tech lead báo đã gửi nhận xét trong hệ thống hiện tại.",
        "not_happened_notice": "Tech lead báo buổi phỏng vấn không diễn ra. Đã dừng nhắc gửi nhận xét.",
        "clarify": "Vui lòng cung cấp mã ứng viên, ngày và giờ bắt đầu/kết thúc (hoặc thời lượng), và @tech-lead phụ trách. Không thể gửi giờ đã qua hoặc liên kết không hợp lệ.",
        "choose_lead": "Vui lòng @tech-lead đã được cấu hình hoặc cung cấp đúng Slack member ID.",
        "choose_interview": "Vui lòng trả lời trong luồng phỏng vấn hoặc cung cấp đầy đủ mã yêu cầu.",
        "provider_error": "Tôi chưa hiểu được tin nhắn này. Chưa gửi hay thay đổi yêu cầu nào. Vui lòng thử lại; các lịch nhắc hiện tại vẫn được giữ.",
        "unavailable": "Bạn không thể truy cập yêu cầu hoặc thao tác này.",
        "stale": "Nút này đã hết hiệu lực. Hãy hỏi trạng thái để nhận thẻ mới nhất.",
        "discarded": "Đã bỏ bản nháp. Chưa gửi yêu cầu nào.",
        "edit_help": "Trả lời trong luồng này với thông tin cần sửa. Tôi sẽ hiển thị thẻ để bạn kiểm tra trước khi gửi.",
        "withdrawn": "HR đã rút yêu cầu phỏng vấn trước đó của bạn. Các nút và lịch nhắc cũ đã hết hiệu lực.",
        "help": "HR: gửi mã ứng viên, ngày giờ, thời lượng và @tech-lead để soạn yêu cầu. Trả lời trong luồng để sửa lịch. Hỏi trạng thái để xem yêu cầu. Tech lead: dùng nút xác nhận và cập nhật trạng thái gửi nhận xét. Gửi nhận xét trong hệ thống hiện tại, không gửi nội dung nhận xét ở đây.",
        "info": "Bạn có thể hỏi hướng dẫn hoặc trạng thái.",
        "send": "Gửi yêu cầu",
        "send_cancel": "Hủy phỏng vấn",
        "draft_edit": "Sửa",
        "discard": "Bỏ bản nháp",
        "confirm": "Xác nhận",
        "change": "Yêu cầu đổi lịch",
        "edit": "Đổi thời gian",
        "cancel_review": "Hủy",
        "feedback": "Đã gửi nhận xét",
        "not_happened": "Phỏng vấn không diễn ra",
        "state": "Trạng thái",
        "ref": "Mã ứng viên",
        "lead": "Tech lead",
        "request": "Yêu cầu",
        "issues": "Cần kiểm tra việc gửi tin",
    },
}

STATES = {
    "pending_confirmation": ("Awaiting lead confirmation", "Chờ tech lead xác nhận"),
    "confirmed": ("Tech lead confirmed", "Tech lead đã xác nhận"),
    "change_requested": ("Change requested", "Cần đổi lịch"),
    "cancelled": ("Cancelled", "Đã hủy"),
    "expired": ("Expired", "Hết hạn"),
    "feedback_pending": ("Awaiting feedback submission status", "Chờ trạng thái gửi nhận xét"),
    "feedback_submitted": ("Feedback reported submitted", "Đã báo gửi nhận xét"),
    "not_happened": ("Reported not held", "Đã báo không diễn ra"),
}


def button(action, label, value):
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": "jh:" + action,
        "value": value,
    }


def render(note, interview, draft, language, issues):
    vi = language == "vi"
    copy = COPY["vi" if vi else "en"]
    title = copy.get(note.kind, copy["info"])
    buttons, details = [], []
    data = draft.data if draft else interview.data if interview else None
    if note.kind == "status" and interview:
        title = STATES.get(interview.state, (interview.state, interview.state))[vi]
    if draft and draft.kind == "cancel":
        title = copy["cancel_draft"]
    if note.kind == "clarify" and note.payload.get("question"):
        title = note.payload["question"][:500]
    if data:
        details.extend(
            [
                f"{copy['ref']}: {data.get('candidate_ref', '—')}",
                f"{copy['lead']}: {data.get('lead_id', '—')}",
            ]
        )
        for field in ("starts_at", "ends_at"):
            if data.get(field):
                try:
                    date = datetime.fromisoformat(data[field])
                    if date.tzinfo is None:
                        continue
                    date = date.astimezone(ZONE)
                except (ValueError, OverflowError):
                    continue
                label = (
                    ("Bắt đầu" if field == "starts_at" else "Kết thúc")
                    if vi
                    else ("Start" if field == "starts_at" else "End")
                )
                details.append(f"{label}: {date:%a %Y-%m-%d %H:%M} (Asia/Ho_Chi_Minh, UTC+07)")
        for field in ("meeting_url", "feedback_url"):
            if data.get(field):
                details.append(data[field])
    if interview:
        details.append(f"{copy['request']}: {interview.id}")
        details.append(f"{copy['state']}: {STATES[interview.state][vi]} · v{interview.version}")
        value = f"{interview.id}:{interview.version}"
        if note.recipient == interview.owner_id:
            if interview.state not in {"cancelled", "feedback_submitted", "not_happened"}:
                buttons = [button(name, copy[name], value) for name in ("edit", "cancel_review")]
        elif note.recipient == interview.lead_id:
            if interview.state == "pending_confirmation":
                buttons = [button(name, copy[name], value) for name in ("confirm", "change")]
            elif interview.state == "confirmed":
                buttons = [button(name, copy[name], value) for name in ("change", "not_happened")]
            elif interview.state == "feedback_pending":
                buttons = [button(name, copy[name], value) for name in ("feedback", "not_happened")]
    if draft and note.kind == "draft":
        buttons = [
            button("send", copy["send_cancel" if draft.kind == "cancel" else "send"], draft.id),
            button("draft_edit", copy["draft_edit"], draft.id),
            button("discard", copy["discard"], draft.id),
        ]
    if note.kind == "withdrawn" and note.payload.get("request_id"):
        details.append(
            f"{copy['request']}: {note.payload['request_id']} · v{note.payload['request_version']}"
        )
        details.append(f"{copy['ref']}: {note.payload['candidate_ref']}")
    if issues:
        details.append(copy["issues"] + ": " + "; ".join(issues)[:500])
    blocks = [{"type": "section", "text": {"type": "plain_text", "text": title[:2900]}}]
    if details:
        # Plain-text blocks prevent candidate strings from creating mentions/markup.
        detail_text = "\n".join(details)
        for start in range(0, len(detail_text), 2900):
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "text": detail_text[start : start + 2900],
                    },
                }
            )
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return {
        "text": "\n".join([title, *details])[:3900],
        "blocks": blocks,
        "mrkdwn": False,
        "unfurl_links": False,
        "unfurl_media": False,
        "parse": "none",
    }
