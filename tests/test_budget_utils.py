from datetime import UTC, datetime, timedelta

from gateway.db import create_tables, make_engine, make_session_factory
from gateway.models import Team, UsageRecord
from gateway.services import seconds_until_utc_midnight, tokens_used_today


def test_seconds_until_utc_midnight():
    now = datetime(2026, 11, 25, 23, 0, tzinfo=UTC)
    assert seconds_until_utc_midnight(now) == 3600.0


async def test_tokens_used_today_ignores_earlier_days():
    engine = make_engine("sqlite+aiosqlite://")
    await create_tables(engine)
    factory = make_session_factory(engine)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    async with factory() as session:
        team = Team(name="t")
        session.add(team)
        await session.commit()
        for when, tokens in [
            (now - timedelta(days=1), 100),
            (now - timedelta(hours=1), 7),
            (now, 3),
        ]:
            session.add(
                UsageRecord(
                    team_id=team.id,
                    request_id="r",
                    provider="p",
                    model="m",
                    prompt_tokens=tokens,
                    completion_tokens=0,
                    created_at=when,
                )
            )
        await session.commit()
        assert await tokens_used_today(session, team.id, now=now) == 10
    await engine.dispose()
