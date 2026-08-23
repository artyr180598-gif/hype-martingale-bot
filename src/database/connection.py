"""
Database Connection and Session Lifecycle Management.
"""
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import settings
from src.core.logging import get_logger
from src.database.models import Base

logger = get_logger("database.connection")

# Engine options depending on dialect
engine_kwargs: dict[str, Any] = {"echo": settings.DB_ECHO}

if "sqlite" in settings.DATABASE_URL:
    # SQLite async specifics
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL / Timescale specifics
    engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
    engine_kwargs["pool_pre_ping"] = True

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    """Initialize database tables and indexes."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schema synchronized successfully", db_url=settings.DATABASE_URL.split("@")[-1])
    except Exception as e:
        logger.error("Failed to initialize database", error=str(e))
        raise


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency / context generator for database sessions."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_health() -> bool:
    """Verify database connection is live."""
    try:
        async with AsyncSessionFactory() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("Database health check failed", error=str(e))
        return False
