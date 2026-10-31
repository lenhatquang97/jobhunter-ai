from datetime import datetime

import pytest
from jobhunter_slack.config import Settings
from jobhunter_slack.domain import add_working_hours, next_working_day, work_opening
from jobhunter_slack.store import Draft, Inbox, Interview, Notification
from pydantic import ValidationError

from .conftest import action, create_interview, message, rows


def dt(value):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize(
    "start,want",
    [
        ("2026-11-02T09:00:00+07:00", "2026-11-02T13:00:00+07:00"),
        ("2026-11-06T16:00:00+07:00", "2026-11-09T11:00:00+07:00"),
        ("2026-11-07T10:00:00+07:00", "2026-11-09T13:00:00+07:00"),
        ("2026-11-02T18:00:00+07:00", "2026-11-03T13:00:00+07:00"),
    ],
)
def test_four_working_hours_exclude_nights_and_weekends(start, want):
    assert add_working_hours(dt(start), 4) == dt(want)


def test_feedback_after_hours_and_next_day_do_not_share_deadline():
    request = work_opening(dt("2026-11-06T19:00:00+07:00"))
    assert request == dt("2026-11-09T09:00:00+07:00")
    assert next_working_day(request) == dt("2026-11-10T09:00:00+07:00")


def test_production_rejects_sqlite_and_empty_roles():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite://",
            team_id="TTEST",
            hr_user_ids=["UHR"],
            lead_user_ids=["ULEAD"],
        )


async def test_hr_review_is_required_before_any_lead_message(coordinator, store):
    await message(coordinator)
    assert not await rows(store, Interview)
    notes = await rows(store, Notification)
    assert notes and all(n.recipient == "UHR" for n in notes)
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id)
    interview = (await rows(store, Interview))[0]
    assert interview.state == "pending_confirmation"
    assert interview.lead_id == "ULEAD"
    assert any(
        n.recipient == "ULEAD" and n.kind == "proposal" for n in await rows(store, Notification)
    )


async def test_unknown_workspace_and_user_are_rejected_before_ai(coordinator, store, interpreter):
    assert not await coordinator.enqueue_message("bad", "TOTHER", "UHR", "D", "1", None, "x")
    assert not await message(coordinator, user="USTRANGER")
    assert not interpreter.calls
    assert not await rows(store, Inbox)


async def test_other_hr_and_unassigned_lead_cannot_act(coordinator, store):
    interview = await create_interview(coordinator, store)
    value = f"{interview.id}:1"
    await action(coordinator, "confirm", value, "ULEAD2")
    await action(coordinator, "cancel_review", value, "UOTHER")
    assert (await rows(store, Interview))[0].state == "pending_confirmation"
    assert not await rows(store, Draft)


async def test_confirmation_schedules_both_people_and_uses_real_deadlines(coordinator, store):
    interview = await create_interview(coordinator, store)
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    assert (await rows(store, Interview))[0].state == "confirmed"
    reminders = [n for n in await rows(store, Notification) if n.kind.startswith("reminder_")]
    assert {(n.recipient, n.kind) for n in reminders} == {
        ("UHR", "reminder_24h"),
        ("ULEAD", "reminder_24h"),
        ("UHR", "reminder_30m"),
        ("ULEAD", "reminder_30m"),
    }
    assert {n.due_at.isoformat()[:19] for n in reminders} == {
        "2026-11-03T07:00:00",
        "2026-11-04T06:30:00",
    }


async def test_duplicate_inbox_and_buttons_do_not_duplicate_interviews(coordinator, store):
    await message(coordinator)
    assert not await message(coordinator)
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id)
    await action(coordinator, "send", draft.id, key="different-click")
    assert len(await rows(store, Interview)) == 1


async def test_change_request_invalidates_old_confirm_button(coordinator, store):
    interview = await create_interview(coordinator, store)
    await action(coordinator, "change", f"{interview.id}:1", "ULEAD")
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    current = (await rows(store, Interview))[0]
    assert (current.state, current.version) == ("change_requested", 2)


async def test_rescheduling_requires_review_and_fresh_lead_confirmation(
    coordinator,
    store,
    interpreter,
):
    interview = await create_interview(coordinator, store)
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    interpreter.result = interpreter.result.model_copy(
        update={
            "interview_id": interview.id,
            "starts_at": dt("2026-11-05T14:00:00+07:00"),
            "ends_at": dt("2026-11-05T15:00:00+07:00"),
        }
    )
    await message(coordinator, text="move it", key="E2")
    assert (await rows(store, Interview))[0].version == 1
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id, key="new-time")
    current = (await rows(store, Interview))[0]
    assert (current.state, current.version) == ("pending_confirmation", 2)
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD", key="old-card")
    assert (await rows(store, Interview))[0].state == "pending_confirmation"
    await coordinator.tick()
    assert all(
        n.status == "cancelled"
        for n in await rows(store, Notification)
        if n.interview_id == interview.id and n.version == 1 and n.kind == "reminder_24h"
    )


async def test_cancellation_requires_owner_review_and_stops_reminders(coordinator, store):
    interview = await create_interview(coordinator, store)
    await action(coordinator, "cancel_review", f"{interview.id}:1")
    draft = (await rows(store, Draft))[0]
    assert (await rows(store, Interview))[0].state == "pending_confirmation"
    await action(coordinator, "send", draft.id, key="cancel-send")
    assert (await rows(store, Interview))[0].state == "cancelled"
    await coordinator.tick()
    assert all(
        n.status == "cancelled"
        for n in await rows(store, Notification)
        if n.interview_id == interview.id and n.version == 1
    )


async def test_pending_proposal_expires_at_start_and_cannot_be_accepted_late(
    coordinator,
    store,
    clock,
):
    interview = await create_interview(coordinator, store)
    clock.value = dt("2026-11-04T14:00:00+07:00")
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    await coordinator.tick()
    assert (await rows(store, Interview))[0].state == "expired"
    assert not any(n.kind == "feedback_request" for n in await rows(store, Notification))


async def test_feedback_is_only_self_reported_by_assigned_lead(coordinator, store, clock):
    interview = await create_interview(coordinator, store)
    await action(coordinator, "confirm", f"{interview.id}:1", "ULEAD")
    clock.value = dt("2026-11-04T15:01:00+07:00")
    await coordinator.tick()
    assert (await rows(store, Interview))[0].state == "feedback_pending"
    await action(coordinator, "feedback", f"{interview.id}:1", "UHR")
    assert (await rows(store, Interview))[0].state == "feedback_pending"
    await action(coordinator, "feedback", f"{interview.id}:1", "ULEAD")
    assert (await rows(store, Interview))[0].state == "feedback_submitted"


async def test_past_or_unconfigured_lead_never_produces_sendable_draft(
    coordinator,
    store,
    interpreter,
):
    interpreter.result = interpreter.result.model_copy(update={"lead_id": "USTRANGER"})
    await message(coordinator)
    assert not await rows(store, Draft)
    assert not await rows(store, Interview)


async def test_ai_cannot_pick_a_lead_that_hr_did_not_mention(coordinator, store):
    await message(coordinator, text="Interview APP-123 on Wednesday")
    assert not await rows(store, Draft)
    assert not await rows(store, Interview)


async def test_missing_details_are_preserved_but_cannot_be_sent(coordinator, store, interpreter):
    interpreter.result = interpreter.result.model_copy(update={"ends_at": None})
    await message(coordinator)
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id)
    assert not await rows(store, Interview)
    assert any(n.kind == "clarify" for n in await rows(store, Notification))


async def test_hr_cannot_send_a_request_to_themself(coordinator, store, settings, interpreter):
    settings.lead_user_ids.append("UHR")
    interpreter.result = interpreter.result.model_copy(update={"lead_id": "UHR"})
    await message(coordinator, text="Interview APP-123 with <@UHR>")
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id)
    assert not await rows(store, Interview)


@pytest.mark.parametrize(
    "start,end",
    [
        ("9999-12-31T23:30:00-12:00", "9999-12-31T23:59:00-12:00"),
        ("9999-12-31T22:30:00+00:00", "9999-12-31T23:00:00+00:00"),
    ],
)
async def test_extreme_provider_dates_cannot_poison_the_durable_inbox(
    coordinator,
    store,
    interpreter,
    start,
    end,
):
    interpreter.result = interpreter.result.model_copy(
        update={
            "starts_at": dt(start),
            "ends_at": dt(end),
        }
    )
    await message(coordinator)
    assert (await rows(store, Inbox))[0].status == "done"
    assert not await rows(store, Interview)
    assert any(n.kind == "clarify" for n in await rows(store, Notification))
