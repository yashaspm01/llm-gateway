"""Admin API: create teams, issue and revoke keys, read usage."""

from fastapi import APIRouter, Depends, Request

from gateway import services
from gateway.deps import SessionDep, require_admin
from gateway.schemas import ApiKeyCreated, TeamCreate, TeamOut, UsageOut

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/teams", response_model=TeamOut, status_code=201)
async def create_team(body: TeamCreate, session: SessionDep) -> TeamOut:
    team = await services.create_team(session, body.name)
    return TeamOut(id=team.id, name=team.name)


@router.post("/teams/{team_id}/keys", response_model=ApiKeyCreated, status_code=201)
async def create_key(team_id: int, request: Request, session: SessionDep) -> ApiKeyCreated:
    pepper = request.app.state.settings.api_key_pepper.get_secret_value()
    row, plaintext = await services.create_api_key(session, team_id, pepper)
    return ApiKeyCreated(id=row.id, team_id=row.team_id, prefix=row.prefix, api_key=plaintext)


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: int, session: SessionDep) -> None:
    await services.revoke_api_key(session, key_id)


@router.get("/teams/{team_id}/usage", response_model=UsageOut)
async def team_usage(team_id: int, session: SessionDep) -> UsageOut:
    return UsageOut(**await services.team_usage(session, team_id))
