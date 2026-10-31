import json

from jobhunter_slack.runtime import build_app
from jobhunter_slack.store import Inbox
from slack_bolt.request.async_request import AsyncBoltRequest

from .conftest import rows


async def test_socket_event_is_persisted_before_ack_and_not_acted_on(coordinator, settings, store):
    app = build_app(settings, coordinator, "UBOT")
    body = {
        "type": "event_callback",
        "team_id": "TTEST",
        "api_app_id": "ATEST",
        "event_id": "EVLIVE",
        "event_time": 1793584800,
        "authorizations": [
            {
                "team_id": "TTEST",
                "user_id": "UBOT",
                "is_bot": True,
                "is_enterprise_install": False,
                "enterprise_id": None,
            }
        ],
        "event": {
            "type": "message",
            "channel_type": "im",
            "user": "UHR",
            "channel": "DUHR",
            "ts": "1793584800.123456",
            "text": "hello",
        },
    }
    response = await app.async_dispatch(AsyncBoltRequest(body=json.dumps(body), mode="socket_mode"))
    assert response.status == 200
    inbox = await rows(store, Inbox)
    assert len(inbox) == 1 and inbox[0].status == "pending"


async def test_socket_edits_bots_and_channel_messages_do_not_create_work(
    coordinator,
    settings,
    store,
):
    app = build_app(settings, coordinator, "UBOT")
    for event in [
        {"type": "message", "channel_type": "im", "subtype": "message_changed"},
        {"type": "message", "channel_type": "im", "bot_id": "BBOT"},
        {"type": "message", "channel_type": "channel", "user": "UHR", "text": "hello"},
    ]:
        body = {"type": "event_callback", "team_id": "TTEST", "event_id": "ignored", "event": event}
        response = await app.async_dispatch(
            AsyncBoltRequest(
                body=json.dumps(body),
                mode="socket_mode",
            )
        )
        assert response.status == 200
    assert not await rows(store, Inbox)


async def test_member_removed_after_queueing_cannot_trigger_ai(coordinator, store, interpreter):
    await coordinator.enqueue_message("Eremoved", "TTEST", "UHR", "DUHR", "1", None, "hello")

    async def inactive(user):
        return False

    coordinator.member_checker = inactive
    await coordinator.process_one()
    assert not interpreter.calls
    assert (await rows(store, Inbox))[0].status == "rejected"


async def test_button_reply_uses_original_thread_root(coordinator, settings, store):
    app = build_app(settings, coordinator, "UBOT")
    body = {
        "type": "block_actions",
        "team": {"id": "TTEST"},
        "user": {"id": "UHR"},
        "container": {"type": "message", "channel_id": "DUHR", "message_ts": "2"},
        "message": {"ts": "2", "thread_ts": "1"},
        "actions": [{"action_id": "jh:discard", "action_ts": "3", "value": "a" * 32}],
    }
    response = await app.async_dispatch(AsyncBoltRequest(body=json.dumps(body), mode="socket_mode"))
    assert response.status == 200
    assert (await rows(store, Inbox))[0].payload["ts"] == "1"
