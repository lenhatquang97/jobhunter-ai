from datetime import timedelta
from types import SimpleNamespace

import pytest
from jobhunter_slack.delivery import DeliveryError, DeliveryWorker, SlackGateway
from jobhunter_slack.provider import Interpreter, ProviderError
from jobhunter_slack.store import Draft, Interview, Notification, Thread
from jobhunter_slack.views import render
from sqlalchemy import select

from .conftest import action, create_interview, message, rows
from .test_coordination import dt


class Gateway:
    def __init__(self):
        self.messages = []
        self.failure = None
        self.inactive = set()

    async def active(self, user):
        return user not in self.inactive

    async def post(self, user, body, channel=None, thread_ts=None):
        if self.failure:
            raise self.failure
        self.messages.append({"user": user, "body": body, "thread": thread_ts})
        return {"channel": channel or "D" + user, "ts": str(len(self.messages))}


@pytest.fixture
def gateway():
    return Gateway()


@pytest.fixture
def delivery(settings, store, gateway, clock):
    return DeliveryWorker(settings, store, gateway, clock)


async def drain(coordinator, delivery):
    await coordinator.tick()
    for _ in range(100):
        if not await delivery.deliver_one():
            return
    pytest.fail("Delivery loop did not drain")


async def test_real_flow_renders_actionable_private_cards(coordinator, store, delivery, gateway):
    await message(coordinator)
    await drain(coordinator, delivery)
    first = gateway.messages[0]
    assert first["user"] == "UHR"
    buttons = first["body"]["blocks"][-1]["elements"]
    assert {b["action_id"] for b in buttons} == {"jh:send", "jh:draft_edit", "jh:discard"}
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id)
    await drain(coordinator, delivery)
    request = next(m for m in gateway.messages if m["user"] == "ULEAD")
    assert "APP-123" in request["body"]["text"]
    assert "Asia/Ho_Chi_Minh" in request["body"]["text"]
    assert {b["action_id"] for b in request["body"]["blocks"][-1]["elements"]} == {
        "jh:confirm",
        "jh:change",
    }
    assert len(await rows(store, Thread)) == 2


async def test_restart_coalesces_missed_preinterview_reminders(
    coordinator,
    store,
    delivery,
    gateway,
    clock,
):
    interview = await create_interview(coordinator, store)
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    await drain(coordinator, delivery)
    gateway.messages.clear()
    clock.value = dt("2026-11-04T13:45:00+07:00")
    await store.recover()
    await drain(coordinator, delivery)
    assert len(gateway.messages) == 2
    assert {m["user"] for m in gateway.messages} == {"UHR", "ULEAD"}
    notes = await rows(store, Notification)
    assert all(n.status == "cancelled" for n in notes if n.kind == "reminder_24h")


async def test_feedback_followup_is_based_on_actual_delivery_after_downtime(
    coordinator,
    store,
    delivery,
    clock,
):
    interview = await create_interview(coordinator, store)
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    clock.value = dt("2026-11-09T10:00:00+07:00")
    await drain(coordinator, delivery)
    pending = [n for n in await rows(store, Notification) if n.kind == "feedback_followup"]
    assert {n.recipient for n in pending} == {"UHR", "ULEAD"}
    assert all(n.due_at.isoformat()[:19] == "2026-11-10T02:00:00" for n in pending)
    assert all(n.status == "pending" for n in pending)


async def test_crash_during_send_is_visible_and_not_automatically_resent(
    coordinator,
    store,
    delivery,
    gateway,
):
    interview = await create_interview(coordinator, store)
    async with store.sessions.begin() as session:
        note = await session.scalar(select(Notification).where(Notification.kind == "proposal"))
        note.status = "sending"
    await store.recover()
    await drain(coordinator, delivery)
    assert not any(m["user"] == "ULEAD" for m in gateway.messages)
    uncertain = [n for n in await rows(store, Notification) if n.status == "uncertain"]
    assert len(uncertain) == 1 and uncertain[0].interview_id == interview.id


async def test_rate_limit_is_retried_after_slack_deadline_without_early_resend(
    coordinator,
    store,
    delivery,
    gateway,
    clock,
):
    await message(coordinator)
    gateway.failure = DeliveryError("rate_limited", retry_after=42)
    assert await delivery.deliver_one()
    assert not await delivery.deliver_one()
    gateway.failure = None
    clock.value += timedelta(seconds=42)
    assert await delivery.deliver_one()
    assert len(gateway.messages) == 1
    assert (await rows(store, Notification))[0].status == "sent"


async def test_ambiguous_network_send_is_not_retried_and_logs_no_candidate_data(
    coordinator,
    store,
    delivery,
    gateway,
    caplog,
):
    await message(coordinator)
    gateway.failure = DeliveryError("delivery_uncertain", uncertain=True)
    await delivery.deliver_one()
    assert not await delivery.deliver_one()
    assert (await rows(store, Notification))[0].status == "uncertain"
    assert "APP-123" not in caplog.text
    assert "xoxb-test" not in caplog.text


async def test_inactive_participant_receives_no_reminder(coordinator, store, delivery, gateway):
    await create_interview(coordinator, store)
    gateway.inactive.add("ULEAD")
    await drain(coordinator, delivery)
    assert not any(m["user"] == "ULEAD" for m in gateway.messages)


async def test_vietnamese_cards_do_not_interpret_candidate_text_as_slack_markup(
    coordinator,
    store,
    delivery,
    gateway,
    interpreter,
):
    interpreter.result = interpreter.result.model_copy(
        update={
            "language": "vi",
            "candidate_ref": "APP-123 <!channel>",
        }
    )
    await message(coordinator)
    await drain(coordinator, delivery)
    body = gateway.messages[0]["body"]
    assert "Gửi yêu cầu" in str(body)
    assert body["blocks"][1]["text"]["type"] == "plain_text"
    assert body["mrkdwn"] is False


async def test_provider_failure_keeps_worker_and_draft_safe(
    coordinator,
    store,
    delivery,
    gateway,
    interpreter,
):
    async def fail(*args):
        raise RuntimeError("secret provider error")

    interpreter.parse = fail
    await message(coordinator)
    await drain(coordinator, delivery)
    assert not await rows(store, Interview)
    assert "secret" not in gateway.messages[0]["body"]["text"]


async def test_structured_provider_uses_no_tools_or_server_side_storage(settings, interpreter):
    captured = {}

    async def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=interpreter.result)

    provider = Interpreter(settings, client=SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    result = await provider.parse(
        "Interview with <@ULEAD>", {"role": "hr"}, dt("2026-11-02T09:00:00+07:00")
    )
    assert result.lead_id == "ULEAD"
    assert captured["store"] is False and "tools" not in captured
    assert "test" not in captured["instructions"]


async def test_provider_refusal_does_not_fabricate_an_intent(settings):
    async def parse(**kwargs):
        return SimpleNamespace(output_parsed=None)

    provider = Interpreter(settings, client=SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    with pytest.raises(ProviderError):
        await provider.parse("x", {}, dt("2026-11-02T09:00:00+07:00"))


async def test_slack_gateway_enforces_active_internal_members(settings):
    async def user_info(**kwargs):
        return {
            "ok": True,
            "user": {
                "id": kwargs["user"],
                "team_id": "TTEST",
                "is_bot": False,
                "deleted": False,
                "is_restricted": True,
            },
        }

    gateway = SlackGateway(settings, SimpleNamespace(users_info=user_info))
    assert not await gateway.active("ULEAD")


def test_feedback_card_describes_self_report_not_external_verification():
    interview = SimpleNamespace(
        id="a" * 32,
        version=1,
        state="feedback_submitted",
        owner_id="UHR",
        lead_id="ULEAD",
        data={
            "candidate_ref": "APP-123",
            "starts_at": "2026-11-04T07:00:00+00:00",
            "ends_at": "2026-11-04T08:00:00+00:00",
        },
    )
    note = SimpleNamespace(kind="feedback_submitted_notice", recipient="UHR", payload={})
    body = render(note, interview, None, "en", [])
    assert "reported" in body["text"].lower()
    assert "verified" not in body["text"].lower()


async def test_owner_removed_from_hr_role_gets_no_private_status_or_delivery(
    coordinator,
    store,
    delivery,
    gateway,
    settings,
    interpreter,
):
    await create_interview(coordinator, store)
    settings.hr_user_ids.remove("UHR")
    settings.lead_user_ids.append("UHR")
    interpreter.result = interpreter.result.model_copy(update={"kind": "status"})
    await message(coordinator, key="status-after-role-removal")
    await drain(coordinator, delivery)
    assert not any("APP-123" in m["body"]["text"] for m in gateway.messages if m["user"] == "UHR")


def test_incomplete_timezone_never_appears_as_a_valid_vietnam_time():
    draft = SimpleNamespace(
        kind="propose",
        id="d" * 32,
        data={
            "candidate_ref": "APP-123",
            "starts_at": "2026-11-04T14:00:00",
        },
    )
    note = SimpleNamespace(kind="clarify", recipient="UHR", payload={})
    body = render(note, None, draft, "en", [])
    assert "Start:" not in body["text"]


@pytest.mark.parametrize("phase", ["confirmation", "feedback_request", "feedback_followup"])
async def test_overdue_working_hour_notifications_wait_until_monday(
    coordinator,
    store,
    delivery,
    gateway,
    clock,
    interpreter,
    phase,
):
    if phase == "confirmation":
        interpreter.result = interpreter.result.model_copy(
            update={
                "starts_at": dt("2026-11-11T14:00:00+07:00"),
                "ends_at": dt("2026-11-11T15:00:00+07:00"),
            }
        )
    interview = await create_interview(coordinator, store)
    if phase != "confirmation":
        await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    await drain(coordinator, delivery)
    if phase == "feedback_followup":
        clock.value = dt("2026-11-04T15:00:00+07:00")
        await drain(coordinator, delivery)
    gateway.messages.clear()
    clock.value = dt("2026-11-07T10:00:00+07:00")
    await drain(coordinator, delivery)
    assert gateway.messages == []
    working_kinds = {"confirmation_followup", "feedback_request", "feedback_followup"}
    pending = [
        n
        for n in await rows(store, Notification)
        if n.kind in working_kinds and n.status == "pending"
    ]
    assert pending
    assert all(n.due_at.isoformat()[:19] == "2026-11-09T02:00:00" for n in pending)


@pytest.mark.parametrize("remove_role", [False, True])
async def test_reassigned_lead_gets_withdrawal_in_the_original_interview_thread(
    coordinator,
    store,
    delivery,
    gateway,
    interpreter,
    settings,
    remove_role,
):
    interview = await create_interview(coordinator, store)
    await drain(coordinator, delivery)
    old_thread = next(t for t in await rows(store, Thread) if t.recipient == "ULEAD")
    interpreter.result = interpreter.result.model_copy(
        update={
            "interview_id": interview.id,
            "lead_id": "ULEAD2",
        }
    )
    await message(coordinator, text="Change interviewer to <@ULEAD2>", key="E2")
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id, key="reassign")
    if remove_role:
        settings.lead_user_ids.remove("ULEAD")
        settings.hr_user_ids.append("ULEAD")
    gateway.messages.clear()
    await drain(coordinator, delivery)
    if remove_role:
        assert not any(m["user"] == "ULEAD" for m in gateway.messages)
        return
    withdrawn = next(m for m in gateway.messages if m["user"] == "ULEAD")
    assert withdrawn["thread"] == old_thread.thread_ts
    assert interview.id in withdrawn["body"]["text"]
