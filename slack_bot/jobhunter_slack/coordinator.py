import re
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from .domain import (
    TERMINAL,
    Proposal,
    add_working_hours,
    utc,
    work_opening,
)
from .store import Draft, Inbox, Interview, Member, Notification, Thread


def recipient_allowed(interview, user, settings):
    return (user == interview.owner_id and user in settings.hr_user_ids) or (
        user == interview.lead_id and user in settings.lead_user_ids
    )


def eligible(note, interview, draft, now, settings=None):
    if note.kind == "withdrawn" and settings and note.recipient not in settings.lead_user_ids:
        return False
    if note.draft_id:
        return (
            draft is not None
            and draft.owner_id == note.recipient
            and (settings is None or note.recipient in settings.hr_user_ids)
        )
    if not note.interview_id:
        return True
    if (
        interview is None
        or interview.version != note.version
        or note.recipient
        not in {
            interview.owner_id,
            interview.lead_id,
        }
    ):
        return False
    if settings and not recipient_allowed(interview, note.recipient, settings):
        return False
    states = {
        "proposal": {"pending_confirmation"},
        "pending_notice": {"pending_confirmation"},
        "confirmation_followup": {"pending_confirmation"},
        "confirmed_notice": {"confirmed"},
        "change_notice": {"change_requested"},
        "cancelled_notice": {"cancelled"},
        "expired_notice": {"expired"},
        # A future feedback job is valid while the interview is confirmed;
        # _advance moves it to feedback_pending before its due time is consumed.
        "feedback_request": {"confirmed", "feedback_pending"},
        "feedback_followup": {"feedback_pending"},
        "feedback_submitted_notice": {"feedback_submitted"},
        "not_happened_notice": {"not_happened"},
    }
    if note.kind.startswith("reminder_"):
        return interview.state == "confirmed" and utc(interview.starts_at) > utc(now)
    return interview.state in states.get(note.kind, {interview.state})


class Coordinator:
    def __init__(self, settings, store, interpreter, clock=None, member_checker=None):
        self.settings, self.store, self.interpreter = settings, store, interpreter
        self.clock = clock or (lambda: datetime.now(UTC))
        self.member_checker = member_checker

    async def _enqueue(self, key, team, user, kind, payload):
        if not self.settings.permits(team, user) or len(key) > 240:
            return False
        try:
            async with self.store.sessions.begin() as session:
                session.add(
                    Inbox(
                        id=key,
                        user_id=user,
                        kind=kind,
                        payload=payload,
                        created_at=utc(self.clock()),
                    )
                )
            return True
        except IntegrityError:
            return False

    async def enqueue_message(self, key, team, user, channel, ts, thread_ts, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            return False
        return await self._enqueue(
            key,
            team,
            user,
            "message",
            {
                "channel": channel,
                "ts": ts,
                "thread_ts": thread_ts,
                "text": text,
            },
        )

    async def enqueue_action(self, key, team, user, channel, ts, action, value):
        if not isinstance(value, str) or len(value) > 80:
            return False
        return await self._enqueue(
            key,
            team,
            user,
            "action",
            {
                "channel": channel,
                "ts": ts,
                "action": action,
                "value": value,
            },
        )

    async def process_one(self):
        async with self.store.lock, self.store.sessions.begin() as session:
            item = await session.scalar(
                select(Inbox)
                .where(
                    Inbox.status == "pending",
                )
                .order_by(Inbox.created_at, Inbox.id)
                .limit(1)
            )
            if item is None:
                return False
            if item.user_id not in self.settings.participants or (
                self.member_checker and not await self.member_checker(item.user_id)
            ):
                item.status, item.payload = "rejected", {}
                return True
            now = utc(self.clock())
            await self._advance(session, now)
            # Processing and its state/outbox writes commit together. A crash rolls
            # back the inbox claim, so replay cannot leave half-applied actions.
            item.status = "processing"
            if item.kind == "message":
                await self._message(session, item, now)
            else:
                await self._action(session, item, now)
            item.status, item.payload = "done", {}
            return True

    async def _notice(self, session, item, kind="info", payload=None):
        session.add(
            Notification(
                dedup_key=f"inbox:{item.id}",
                recipient=item.user_id,
                kind=kind,
                payload={
                    "channel": item.payload["channel"],
                    "thread_ts": item.payload.get("thread_ts") or item.payload["ts"],
                    **(payload or {}),
                },
                due_at=utc(self.clock()),
            )
        )

    async def _note(self, session, interview, recipient, kind, due=None):
        key = f"{interview.id}:{interview.version}:{kind}:{recipient}"
        if await session.scalar(select(Notification.id).where(Notification.dedup_key == key)):
            return
        session.add(
            Notification(
                dedup_key=key,
                recipient=recipient,
                interview_id=interview.id,
                version=interview.version,
                kind=kind,
                payload={},
                due_at=utc(due or self.clock()),
            )
        )
        await session.flush()

    async def _both(self, session, interview, kind):
        for user in {interview.owner_id, interview.lead_id}:
            await self._note(session, interview, user, kind)

    async def _owned(self, session, user, target):
        if not target or not re.fullmatch(r"[a-f0-9]{32}", target):
            return None
        interview = await session.get(Interview, target)
        return interview if interview and interview.owner_id == user else None

    async def _message(self, session, item, now):
        user, payload = item.user_id, item.payload
        member = await session.get(Member, user)
        if member is None:
            member = Member(id=user, language="en")
            session.add(member)
        visible = list(
            (
                await session.scalars(
                    select(Interview)
                    .where(
                        or_(
                            Interview.owner_id == user
                            if user in self.settings.hr_user_ids
                            else False,
                            Interview.lead_id == user
                            if user in self.settings.lead_user_ids
                            else False,
                        ),
                    )
                    .order_by(Interview.created_at.desc())
                    .limit(30)
                )
            ).all()
        )
        route = (
            await session.scalar(
                select(Thread).where(
                    Thread.recipient == user,
                    Thread.channel == payload["channel"],
                    Thread.thread_ts == payload.get("thread_ts"),
                )
            )
            if payload.get("thread_ts")
            else None
        )
        previous = await session.scalar(select(Draft).where(Draft.owner_id == user))
        context = {
            "role": "hr" if user in self.settings.hr_user_ids else "lead",
            "interviews": [
                {
                    "id": v.id,
                    "candidate_ref": v.data["candidate_ref"],
                    "state": v.state,
                    "lead_id": v.lead_id,
                    "starts_at": v.data["starts_at"],
                    "ends_at": v.data["ends_at"],
                }
                for v in visible
            ],
            "thread_interview_id": route.interview_id if route else None,
            "draft": previous.data if previous else None,
            "language": member.language,
            "allowed_lead_ids": self.settings.lead_user_ids,
        }
        try:
            intent = await self.interpreter.parse(payload["text"], context, now)
        except Exception:
            # No provider exception/text is logged or passed through to Slack.
            await self._notice(session, item, "provider_error")
            return
        member.language = intent.language
        if user not in self.settings.hr_user_ids or intent.kind in {"status", "help"}:
            if visible and intent.kind != "help":
                requested = intent.interview_id or (route.interview_id if route else None)
                selected = [v for v in visible if not requested or v.id == requested]
                for interview in selected:
                    # Each query gets a fresh current-state card, not a one-shot job key.
                    session.add(
                        Notification(
                            dedup_key=f"status:{item.id}:{interview.id}",
                            recipient=user,
                            interview_id=interview.id,
                            version=interview.version,
                            kind="status",
                            payload={},
                            due_at=now,
                        )
                    )
                if selected:
                    return
            await self._notice(session, item, "help")
            return
        target = intent.interview_id or (route.interview_id if route else None)
        if not target and previous:
            target = previous.target_id
        interview = await self._owned(session, user, target)
        if target and interview is None:
            await self._notice(session, item, "unavailable")
            return
        if intent.kind == "cancel" and interview is None:
            await self._notice(session, item, "choose_interview")
            return
        data = dict(interview.data if interview else {})
        if previous and previous.target_id == target:
            data.update(previous.data)
        data.update(intent.proposal_data())
        for field in ("meeting_url", "feedback_url"):
            if data.get(field) == "":
                data[field] = None
        lead = data.get("lead_id")
        known_lead = (previous and previous.data.get("lead_id") == lead) or (
            interview and interview.lead_id == lead
        )
        mentioned = lead and re.search(
            rf"(?<![A-Z0-9]){re.escape(lead)}(?![A-Z0-9])", payload["text"]
        )
        if lead and (lead not in self.settings.lead_user_ids or not (known_lead or mentioned)):
            await self._notice(session, item, "choose_lead")
            return
        if previous:
            await session.delete(previous)
            await session.flush()
        draft = Draft(
            owner_id=user,
            kind=intent.kind,
            target_id=target,
            expected_version=interview.version if interview else None,
            data=data,
            channel=payload["channel"],
            thread_ts=payload.get("thread_ts") or payload["ts"],
            question=intent.question,
        )
        session.add(draft)
        await session.flush()
        if not self._valid_draft(draft, now):
            session.add(
                Notification(
                    dedup_key=f"draft:{draft.id}",
                    recipient=user,
                    draft_id=draft.id,
                    kind="clarify",
                    payload={"question": intent.question},
                    due_at=now,
                )
            )
        else:
            session.add(
                Notification(
                    dedup_key=f"draft:{draft.id}",
                    recipient=user,
                    draft_id=draft.id,
                    kind="draft",
                    payload={},
                    due_at=now,
                )
            )

    def _valid_draft(self, draft, now):
        if draft.kind == "cancel":
            return bool(draft.target_id)
        if draft.question:
            return False
        try:
            proposal = Proposal.model_validate(draft.data)
            return (
                proposal.starts_at > now
                and proposal.lead_id in self.settings.lead_user_ids
                and (proposal.lead_id != draft.owner_id)
            )
        except (ValidationError, ValueError):
            return False

    async def _action(self, session, item, now):
        name, value, user = item.payload["action"], item.payload["value"], item.user_id
        if name in {"send", "discard", "draft_edit"}:
            draft = await session.get(Draft, value)
            if draft is None or draft.owner_id != user or user not in self.settings.hr_user_ids:
                await self._notice(session, item, "unavailable")
                return
            if name == "discard":
                await session.delete(draft)
                await self._notice(session, item, "discarded")
                return
            if name == "draft_edit":
                await self._notice(session, item, "edit_help")
                return
            if not self._valid_draft(draft, now):
                await self._notice(session, item, "clarify")
                return
            interview = await self._owned(session, user, draft.target_id)
            if draft.target_id and (
                interview is None or interview.version != draft.expected_version
            ):
                await self._notice(session, item, "stale")
                return
            if draft.kind == "cancel":
                interview.version += 1
                interview.state = "cancelled"
                await self._both(session, interview, "cancelled_notice")
            else:
                proposal = Proposal.model_validate(draft.data)
                if interview is None:
                    interview = Interview(
                        owner_id=user,
                        lead_id=proposal.lead_id,
                        data={},
                        starts_at=proposal.starts_at,
                        ends_at=proposal.ends_at,
                        created_at=now,
                    )
                    session.add(interview)
                    await session.flush()
                else:
                    old_lead = interview.lead_id
                    if old_lead != proposal.lead_id:
                        old_thread = await session.scalar(
                            select(Thread).where(
                                Thread.interview_id == interview.id,
                                Thread.recipient == old_lead,
                            )
                        )
                        session.add(
                            Notification(
                                dedup_key=f"withdraw:{interview.id}:{interview.version}",
                                recipient=old_lead,
                                kind="withdrawn",
                                payload={
                                    "request_id": interview.id,
                                    "request_version": interview.version,
                                    "candidate_ref": interview.data["candidate_ref"],
                                    "channel": old_thread.channel if old_thread else None,
                                    "thread_ts": old_thread.thread_ts if old_thread else None,
                                },
                                due_at=now,
                            )
                        )
                    interview.version += 1
                interview.data = proposal.model_dump(mode="json")
                interview.lead_id = proposal.lead_id
                interview.starts_at, interview.ends_at = proposal.starts_at, proposal.ends_at
                interview.state, interview.feedback_requested_at = "pending_confirmation", None
                await session.flush()
                owner_thread = await session.scalar(
                    select(Thread).where(
                        Thread.interview_id == interview.id,
                        Thread.recipient == user,
                    )
                )
                if owner_thread is None:
                    session.add(
                        Thread(
                            interview_id=interview.id,
                            recipient=user,
                            channel=draft.channel,
                            thread_ts=draft.thread_ts,
                        )
                    )
                await self._note(session, interview, interview.lead_id, "proposal")
                await self._note(session, interview, user, "pending_notice")
                deadline = add_working_hours(now, 4)
                if deadline < proposal.starts_at:
                    for uid in {user, interview.lead_id}:
                        await self._note(session, interview, uid, "confirmation_followup", deadline)
            await session.delete(draft)
            return
        match = re.fullmatch(r"([a-f0-9]{32}):([1-9][0-9]{0,8})", value)
        interview = await session.get(Interview, match[1]) if match else None
        if interview is None or not recipient_allowed(interview, user, self.settings):
            await self._notice(session, item, "unavailable")
            return
        if interview.version != int(match[2]):
            await self._notice(session, item, "stale")
            return
        if name in {"edit", "cancel_review"} and (
            user == interview.owner_id and user in self.settings.hr_user_ids
        ):
            prior = await session.scalar(select(Draft).where(Draft.owner_id == user))
            if prior:
                await session.delete(prior)
                await session.flush()
            draft = Draft(
                owner_id=user,
                kind="cancel" if name == "cancel_review" else "propose",
                target_id=interview.id,
                expected_version=interview.version,
                data=dict(interview.data),
                channel=item.payload["channel"],
                thread_ts=item.payload["ts"],
            )
            session.add(draft)
            await session.flush()
            session.add(
                Notification(
                    dedup_key=f"draft:{draft.id}",
                    recipient=user,
                    draft_id=draft.id,
                    kind="draft" if name == "cancel_review" else "edit_help",
                    payload={},
                    due_at=now,
                )
            )
            return
        if user != interview.lead_id or user not in self.settings.lead_user_ids:
            await self._notice(session, item, "unavailable")
            return
        if name == "confirm" and interview.state == "pending_confirmation":
            interview.state = "confirmed"
            await self._both(session, interview, "confirmed_notice")
            for kind, delta in (
                ("reminder_24h", timedelta(hours=24)),
                ("reminder_30m", timedelta(minutes=30)),
            ):
                deadline = utc(interview.starts_at) - delta
                if deadline > now:
                    for uid in {interview.owner_id, interview.lead_id}:
                        await self._note(session, interview, uid, kind, deadline)
            await self._note(
                session, interview, user, "feedback_request", work_opening(utc(interview.ends_at))
            )
        elif name == "change" and interview.state in {"pending_confirmation", "confirmed"}:
            if utc(interview.starts_at) <= now:
                await self._notice(session, item, "stale")
                return
            interview.version += 1
            interview.state = "change_requested"
            await self._both(session, interview, "change_notice")
        elif name in {"feedback", "not_happened"} and (
            interview.state == "feedback_pending"
            or (name == "not_happened" and interview.state == "confirmed")
        ):
            interview.state = "feedback_submitted" if name == "feedback" else "not_happened"
            await self._both(session, interview, interview.state + "_notice")
        else:
            await self._notice(session, item, "stale")

    async def _advance(self, session, now):
        interviews = list(
            (
                await session.scalars(
                    select(Interview).where(
                        Interview.state.not_in(TERMINAL),
                    )
                )
            ).all()
        )
        for interview in interviews:
            if interview.state in {"pending_confirmation", "change_requested"} and (
                utc(interview.starts_at) <= now
            ):
                interview.state = "expired"
                await self._note(session, interview, interview.owner_id, "expired_notice")
            elif interview.state == "confirmed" and utc(interview.ends_at) <= now:
                interview.state = "feedback_pending"
        await session.flush()

    async def tick(self):
        async with self.store.lock, self.store.sessions.begin() as session:
            now = utc(self.clock())
            await self._advance(session, now)
            notes = list(
                (
                    await session.scalars(
                        select(Notification).where(
                            Notification.status == "pending",
                        )
                    )
                ).all()
            )
            for note in notes:
                interview = (
                    await session.get(Interview, note.interview_id) if (note.interview_id) else None
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
