from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from core.config import app_settings


Base = declarative_base()

engine = create_async_engine(
    app_settings.database_dsn, echo=app_settings.echo_queries, future=True
)
async_session = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
