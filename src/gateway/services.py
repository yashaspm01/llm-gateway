"""Database operations. Routes call these and never write queries themselves."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.errors import ConflictError, NotFoundError
from gateway.models import ApiKey, Team, UsageRecord, utcnow
from gateway.schemas import ChatResponse
from gateway.security import generate_api_key, hash_api_key


async def create_team(session: AsyncSession, name: str) -> Team:
    team = Team(name=name)
    session.add(team)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"Team '{name}' already exists") from exc
    return team


async def create_api_key(session: AsyncSession, team_id: int, pepper: str) -> tuple[ApiKey, str]:
    if await session.get(Team, team_id) is None:
        raise NotFoundError(f"Team {team_id} not found")
    plaintext, prefix, key_hash = generate_api_key(pepper)
    row = ApiKey(team_id=team_id, prefix=prefix, key_hash=key_hash)
    session.add(row)
    await session.commit()
    return row, plaintext


async def revoke_api_key(session: AsyncSession, key_id: int) -> None:
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise NotFoundError(f"Key {key_id} not found")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        await session.commit()


async def authenticate_key(session: AsyncSession, token: str, pepper: str) -> Team | None:
    stmt = (
        select(Team)
        .join(ApiKey, ApiKey.team_id == Team.id)
        .where(ApiKey.key_hash == hash_api_key(token, pepper), ApiKey.revoked_at.is_(None))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def record_usage(
    session: AsyncSession,
    *,
    team_id: int,
    request_id: str,
    provider: str,
    response: ChatResponse,
) -> None:
    session.add(
        UsageRecord(
            team_id=team_id,
            request_id=request_id,
            provider=provider,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
    )
    await session.commit()


async def team_usage(session: AsyncSession, team_id: int) -> dict[str, int]:
    if await session.get(Team, team_id) is None:
        raise NotFoundError(f"Team {team_id} not found")
    stmt = select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
        func.coalesce(func.sum(UsageRecord.completion_tokens), 0),
    ).where(UsageRecord.team_id == team_id)
    requests, prompt, completion = (await session.execute(stmt)).one()
    return {
        "requests": int(requests),
        "prompt_tokens": int(prompt),
        "completion_tokens": int(completion),
        "total_tokens": int(prompt) + int(completion),
    }
