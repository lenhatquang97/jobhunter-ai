import os
from datetime import datetime

import pytest
from jobhunter_slack.config import Settings
from jobhunter_slack.coordinator import Coordinator
from jobhunter_slack.domain import Intent
from jobhunter_slack.store import Base, Store
from sqlalchemy import select


class Clock:
    def __init__(self):
        self.value = datetime.fromisoformat("2026-11-02T09:00:00+07:00")

    def __call__(self):
        return self.value


class Interpreter:
    def __init__(self):
        self.result = Intent(
            kind="propose",
            language="en",
            interview_id=None,
            candidate_ref="APP-123",
            lead_id="ULEAD",
            starts_at="2026-11-04T14:00:00+07:00",
            ends_at="2026-11-04T15:00:00+07:00",
            meeting_url=None,
            feedback_url=None,
            question=None,
        )
        self.calls = []

    async def parse(self, text, context, now):
        self.calls.append((text, context))
        return self.result


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        bot_token="xoxb-test",
        app_token="xapp-test",
        team_id="TTEST",
        hr_user_ids=["UHR", "UOTHER"],
        lead_user_ids=["ULEAD", "ULEAD2"],
        database_url="sqlite+aiosqlite://",
        allow_sqlite=True,
        openai_api_key="test",
    )


@pytest.fixture
async def store(settings):
    url = os.getenv("JOBHUNTER_TEST_POSTGRES_URL", settings.database_url)
    database = Store(url)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await database.initialize()
    yield database
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await database.close()


@pytest.fixture
def interpreter():
    return Interpreter()


@pytest.fixture
def coordinator(settings, store, interpreter, clock):
    return Coordinator(settings, store, interpreter, clock)


async def rows(store, model):
    async with store.sessions() as session:
        return list((await session.scalars(select(model))).all())


async def message(
    coordinator, user="UHR", text="interview APP-123 with <@ULEAD>", key="E1", thread=None
):
    accepted = await coordinator.enqueue_message(
        key,
        "TTEST",
        user,
        "D" + user,
        key,
        thread,
        text,
    )
    await coordinator.process_one()
    return accepted


async def action(coordinator, name, value, user="UHR", key=None):
    accepted = await coordinator.enqueue_action(
        key or f"{name}-{value}-{user}",
        "TTEST",
        user,
        "D" + user,
        "1",
        name,
        value,
    )
    await coordinator.process_one()
    return accepted


async def create_interview(coordinator, store):
    from jobhunter_slack.store import Draft, Interview

    await message(coordinator)
    draft = (await rows(store, Draft))[0]
    await action(coordinator, "send", draft.id)
    return (await rows(store, Interview))[0]
