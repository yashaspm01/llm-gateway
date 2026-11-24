"""Database engine and sessions."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from gateway.models import Base


def make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        # One shared connection: required for in-memory SQLite, fine for a local file.
        return create_async_engine(url, poolclass=StaticPool)
    return create_async_engine(url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
