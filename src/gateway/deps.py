"""FastAPI dependencies: how a request gets its session, provider, team and admin check."""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.errors import AuthenticationError, NotFoundError
from gateway.models import Team
from gateway.providers.base import Provider
from gateway.services import authenticate_key


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
    team = await authenticate_key(session, token.strip(), pepper)
    if team is None:
        raise AuthenticationError("Invalid API key")
    return team


TeamDep = Annotated[Team, Depends(require_team)]


def require_admin(request: Request, x_admin_token: Annotated[str | None, Header()] = None) -> None:
    expected = request.app.state.settings.admin_token
    if expected is None:
        raise NotFoundError("Not found")  # admin API is switched off
    supplied = (x_admin_token or "").encode()
    if not hmac.compare_digest(supplied, expected.get_secret_value().encode()):
        raise AuthenticationError("Invalid admin token")
