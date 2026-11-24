"""FastAPI dependencies: how a request gets its session, provider, team and admin check."""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway import services
from gateway.errors import (
    AuthenticationError,
    BudgetExceededError,
    NotFoundError,
    RateLimitedError,
)
from gateway.models import Team
from gateway.providers.base import Provider
from gateway.ratelimit import RateLimiter


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_provider(request: Request) -> Provider:
    return request.app.state.provider


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ProviderDep = Annotated[Provider, Depends(get_provider)]


async def require_team(request: Request, session: SessionDep) -> Team:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Missing or malformed Authorization header")
    pepper = request.app.state.settings.api_key_pepper.get_secret_value()
    team = await services.authenticate_key(session, token.strip(), pepper)
    if team is None:
        raise AuthenticationError("Invalid API key")
    return team


TeamDep = Annotated[Team, Depends(require_team)]


async def enforce_limits(request: Request, team: TeamDep, session: SessionDep) -> Team:
    """Authenticated AND within limits. Cheap in-memory check first, database check second."""
    limiter: RateLimiter = request.app.state.rate_limiter
    wait_s = limiter.check(team.id, team.rate_limit_per_minute)
    if wait_s > 0:
        raise RateLimitedError(
            f"Rate limit of {team.rate_limit_per_minute} requests per minute exceeded", wait_s
        )
    if team.daily_token_budget is not None:
        used = await services.tokens_used_today(session, team.id)
        if used >= team.daily_token_budget:
            raise BudgetExceededError(
                f"Daily token budget of {team.daily_token_budget} is used up "
                f"({used} tokens). It resets at 00:00 UTC.",
                services.seconds_until_utc_midnight(),
            )
    return team


LimitedTeamDep = Annotated[Team, Depends(enforce_limits)]


def require_admin(request: Request, x_admin_token: Annotated[str | None, Header()] = None) -> None:
    expected = request.app.state.settings.admin_token
    if expected is None:
        raise NotFoundError("Not found")  # admin API is switched off
    supplied = (x_admin_token or "").encode()
    if not hmac.compare_digest(supplied, expected.get_secret_value().encode()):
        raise AuthenticationError("Invalid admin token")
