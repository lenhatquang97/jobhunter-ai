import logging
from datetime import UTC, datetime, timedelta

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from .coordinator import eligible
from .domain import next_working_day, utc, work_opening
from .store import Draft, Interview, Member, Notification, Thread
from .views import render

log = logging.getLogger("jobhunter_slack.delivery")


class DeliveryError(Exception):
    def __init__(self, code, retry_after=None, uncertain=False):
        super().__init__(code)
        self.code, self.retry_after, self.uncertain = code, retry_after, uncertain


class SlackGateway:
    def __init__(self, settings, client=None):
        self.settings = settings
        # SDK retries could duplicate a post whose server-side outcome is unknown.
        self.client = client or AsyncWebClient(
            token=settings.bot_token.get_secret_value(),
            timeout=10,
            retry_handlers=[],
        )

    async def validate(self):
        try:
            auth = await self.client.auth_test()
            if auth.get("team_id") != self.settings.team_id or not auth.get("bot_id"):
                raise DeliveryError("workspace_mismatch")
            for uid in self.settings.participants:
                if not await self.active(uid):
                    raise DeliveryError("participant_unavailable")
            return auth
        except DeliveryError:
            raise
        except Exception:
            raise DeliveryError("slack_auth_failed") from None

    async def active(self, user):
        if user not in self.settings.participants:
            return False
        try:
            result = await self.client.users_info(user=user)
            member = result["user"]
            return member.get("team_id") == self.settings.team_id and not any(
                member.get(key)
                for key in (
                    "deleted",
                    "is_bot",
                    "is_app_user",
                    "is_restricted",
                    "is_ultra_restricted",
                    "is_stranger",
                )
            )
        except Exception:
            raise DeliveryError("member_check_unavailable", retry_after=30) from None

    async def post(self, user, body, channel=None, thread_ts=None):
        if not channel:
            try:
                result = await self.client.conversations_open(users=user)
                channel = result["channel"]["id"]
            except Exception:
                # Opening a DM does not post a message; a retry cannot duplicate it.
                raise DeliveryError("dm_open_unavailable", retry_after=30) from None
        try:
            result = await self.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                **body,
            )
            return {"channel": result["channel"], "ts": result["ts"]}
        except SlackApiError as exc:
            if exc.response.status_code == 429 or exc.response.get("error") == "ratelimited":
                try:
                    delay = float(exc.response.headers.get("Retry-After", 30))
                except (TypeError, ValueError):
                    delay = 30
                raise DeliveryError("rate_limited", retry_after=max(1, delay)) from None
            raise DeliveryError("slack_post_rejected") from None
        except Exception:
            raise DeliveryError("delivery_uncertain", uncertain=True) from None


def delivery_group(kind):
    if kind in {"proposal", "pending_notice", "confirmation_followup"}:
        return "confirmation"
    if kind in {"confirmed_notice", "reminder_24h", "reminder_30m"}:
        return "upcoming"
    return kind


class DeliveryWorker:
    def __init__(self, settings, store, gateway, clock=None):
        self.settings, self.store, self.gateway = settings, store, gateway
        self.clock = clock or (lambda: datetime.now(UTC))

    async def deliver_one(self):
        # Serialize state changes and delivery rechecks in this single-worker pilot.
        async with self.store.lock:
            now = utc(self.clock())
            async with self.store.sessions.begin() as session:
                notes = list(
                    (
                        await session.scalars(
                            select(Notification)
                            .where(
                                Notification.status == "pending",
                                Notification.due_at <= now,
                            )
                            .order_by(Notification.due_at.desc(), Notification.id)
                        )
                    ).all()
                )
                selected, seen = None, set()
                for note in notes:
                    interview = (
                        await session.get(Interview, note.interview_id)
                        if (note.interview_id)
                        else None
                    )
                    draft = await session.get(Draft, note.draft_id) if note.draft_id else None
                    if note.recipient not in self.settings.participants or not eligible(
                        note,
                        interview,
                        draft,
                        now,
                        self.settings,
                    ):
                        note.status = "cancelled"
                        continue
                    if (
                        note.kind
                        in {
                            "confirmation_followup",
                            "feedback_request",
                            "feedback_followup",
                        }
                        and utc(work_opening(now)) > now
                    ):
                        note.due_at = utc(work_opening(now))
                        continue
                    if note.interview_id:
                        group = (
                            note.interview_id,
                            note.version,
                            note.recipient,
                            delivery_group(note.kind),
                        )
                        if group in seen:
                            note.status = "cancelled"
                            continue
                        seen.add(group)
                    if selected is None:
                        selected = (note, interview, draft)
                if selected is None:
                    return False
                note, interview, draft = selected
                member = await session.get(Member, note.recipient)
                issues = []
                if interview:
                    failed = list(
                        (
                            await session.scalars(
                                select(Notification)
                                .where(
                                    Notification.interview_id == interview.id,
                                    Notification.version == interview.version,
                                    Notification.status.in_(["uncertain", "failed"]),
                                )
                                .limit(10)
                            )
                        ).all()
                    )
                    issues = [f"{n.kind}: {n.status} ({n.error_code})" for n in failed]
                body = render(note, interview, draft, member.language if member else "en", issues)
                thread = (
                    await session.scalar(
                        select(Thread).where(
                            Thread.interview_id == note.interview_id,
                            Thread.recipient == note.recipient,
                        )
                    )
                    if note.interview_id
                    else None
                )
                channel = (
                    thread.channel
                    if thread
                    else draft.channel
                    if draft
                    else (note.payload.get("channel"))
                )
                thread_ts = (
                    thread.thread_ts
                    if thread
                    else draft.thread_ts
                    if draft
                    else (note.payload.get("thread_ts"))
                )
                note.status, note.attempts = "sending", note.attempts + 1
                note_id, user = note.id, note.recipient
            # The sending claim is durable before the API call. A crash now becomes
            # 'uncertain' at recovery, not a blind resend.
            try:
                if not await self.gateway.active(user):
                    raise DeliveryError("participant_unavailable")
                result = await self.gateway.post(user, body, channel, thread_ts)
            except DeliveryError as error:
                async with self.store.sessions.begin() as session:
                    note = await session.get(Notification, note_id)
                    note.error_code = error.code
                    if error.uncertain:
                        note.status = "uncertain"
                    elif error.retry_after is not None and note.attempts < 8:
                        note.status = "pending"
                        note.due_at = now + timedelta(seconds=error.retry_after)
                    else:
                        note.status = "failed"
                log.warning("delivery_issue id=%s code=%s", note_id, error.code)
                return True
            async with self.store.sessions.begin() as session:
                note = await session.get(Notification, note_id)
                note.status, note.slack_ts, note.error_code = "sent", result["ts"], None
                if note.interview_id:
                    thread = await session.scalar(
                        select(Thread).where(
                            Thread.interview_id == note.interview_id,
                            Thread.recipient == user,
                        )
                    )
                    if thread is None:
                        session.add(
                            Thread(
                                interview_id=note.interview_id,
                                recipient=user,
                                channel=result["channel"],
                                thread_ts=result["ts"],
                            )
                        )
                    if note.kind == "feedback_request":
                        interview = await session.get(Interview, note.interview_id)
                        interview.feedback_requested_at = utc(self.clock())
                        deadline = next_working_day(interview.feedback_requested_at)
                        for uid in {interview.owner_id, interview.lead_id}:
                            session.add(
                                Notification(
                                    dedup_key=f"{interview.id}:{interview.version}:feedback_followup:{uid}",
                                    recipient=uid,
                                    interview_id=interview.id,
                                    version=interview.version,
                                    kind="feedback_followup",
                                    payload={},
                                    due_at=utc(deadline),
                                )
                            )
            return True
