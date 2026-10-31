import asyncio
import json
import logging
from contextlib import suppress

from slack_bolt.async_app import AsyncApp
from slack_bolt.authorization import AuthorizeResult
from slack_bolt.response import BoltResponse
from sqlalchemy import text

from .coordinator import Coordinator
from .delivery import DeliveryError, DeliveryWorker, SlackGateway
from .provider import Interpreter
from .store import Store

log = logging.getLogger("jobhunter_slack.runtime")


def build_app(settings, coordinator, bot_user_id):
    async def authorize(enterprise_id, team_id, user_id):
        if team_id != settings.team_id:
            return None
        return AuthorizeResult(
            enterprise_id=enterprise_id,
            team_id=team_id,
            bot_user_id=bot_user_id,
            bot_token=settings.bot_token.get_secret_value(),
        )

    app = AsyncApp(authorize=authorize, process_before_response=True)

    @app.middleware
    async def durable_ingress(body, context, next):
        # This middleware runs before Bolt's auto-ack event listeners. Returning
        # only after the bounded database insert gives Socket Mode a durable ack.
        team = body.get("team_id") or body.get("team", {}).get("id")
        try:
            async with asyncio.timeout(2):
                if body.get("type") == "event_callback":
                    event = body.get("event", {})
                    if (
                        event.get("type") == "message"
                        and event.get("channel_type") == "im"
                        and (
                            not event.get("subtype")
                            and not event.get("bot_id")
                            and event.get("user") != bot_user_id
                        )
                    ):
                        await coordinator.enqueue_message(
                            body.get("event_id", ""),
                            team,
                            event.get("user"),
                            event.get("channel", ""),
                            event.get("ts", ""),
                            event.get("thread_ts"),
                            event.get("text", ""),
                        )
                elif body.get("type") == "block_actions":
                    user = body.get("user", {}).get("id")
                    container = body.get("container", {})
                    if not container.get("channel_id", "").startswith("D"):
                        return BoltResponse(status=200, body="")
                    for action in body.get("actions", [])[:1]:
                        if action.get("action_id", "").startswith("jh:"):
                            # The event carries no credentials or arbitrary business payloads.
                            key = json.dumps(
                                [
                                    team,
                                    user,
                                    action.get("action_ts"),
                                    action.get("action_id"),
                                    action.get("value"),
                                ],
                                separators=(",", ":"),
                            )
                            await coordinator.enqueue_action(
                                key,
                                team,
                                user,
                                container["channel_id"],
                                body.get("message", {}).get("thread_ts")
                                or container.get("message_ts", ""),
                                action["action_id"][3:],
                                action.get("value", ""),
                            )
            return BoltResponse(status=200, body="")
        except Exception:
            log.warning("ingress_unavailable")
            return BoltResponse(status=503, body="")

    @app.error
    async def sanitized_error(error, body, logger):
        log.error("slack_dispatch_failed")

    return app


async def run(settings, check_only=False):
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    for name in ("bot_token", "app_token", "openai_api_key"):
        if not getattr(settings, name).get_secret_value():
            raise DeliveryError("credentials_missing")
    store, provider = Store(settings.database_url), Interpreter(settings)
    gateway = SlackGateway(settings)
    handler, guard, worker = None, None, None
    try:
        auth = await gateway.validate()
        guard = await store.engine.connect()
        locked = await guard.scalar(text("SELECT pg_try_advisory_lock(1824382997)"))
        await guard.commit()
        if not locked:
            raise DeliveryError("worker_already_running")
        if check_only:
            log.info("preflight_ok workspace_members_and_database_checked")
            return
        await store.initialize()
        await store.recover()
        coordinator = Coordinator(settings, store, provider, member_checker=gateway.active)
        delivery = DeliveryWorker(settings, store, gateway)
        app = build_app(settings, coordinator, auth["user_id"])
        handler = AsyncSocketModeHandler(app, app_token=settings.app_token.get_secret_value())

        async def work():
            while True:
                # Loss of the session holding the advisory lock stops this process.
                await guard.execute(text("SELECT 1"))
                await guard.commit()
                try:
                    await coordinator.process_one()
                except DeliveryError as error:
                    log.warning("member_check_delayed code=%s", error.code)
                await coordinator.tick()
                for _ in range(20):
                    if not await delivery.deliver_one():
                        break
                await asyncio.sleep(settings.poll_seconds)

        await handler.connect_async()
        log.info("worker_started local_computer_must_remain_awake")
        worker = asyncio.create_task(work())
        await worker
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await worker
        if handler:
            with suppress(Exception):
                await handler.close_async()
        if guard:
            with suppress(Exception):
                await guard.execute(text("SELECT pg_advisory_unlock(1824382997)"))
                await guard.commit()
            await guard.close()
        await provider.close()
        await store.close()
