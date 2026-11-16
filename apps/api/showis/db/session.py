import asyncio
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

logger = logging.getLogger("showis.db.session")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://showis:change-me@localhost:5432/showis",
)

# API/uzun omurlu surec icin havuzlu engine. Tek event loop varsayimi gecerlidir.
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def create_task_engine(database_url: str | None = None) -> AsyncEngine:
    """Kisa omurlu gorevler (Celery prefork cocuklari, CLI) icin engine olusturur.

    asyncpg baglantilari olusturulduklari event loop'a baglidir. Celery prefork
    cocuk surecinde her gorev ``asyncio.run()`` ile yeni bir loop acip kapattigi
    icin, havuzda kalan baglanti bir sonraki gorevde baska (olu) bir loop'a ait
    olur ve ``cannot perform operation: another operation is in progress``
    hatasi alinir. ``NullPool`` baglantiyi gorev sonunda kapatir; havuzda
    saklanmadigi icin loop'lar arasi sizinti olusmaz.
    """
    return create_async_engine(
        database_url or DATABASE_URL,
        echo=os.getenv("SQL_ECHO", "false").lower() == "true",
        poolclass=NullPool,
        future=True,
    )


@asynccontextmanager
async def task_session(database_url: str | None = None) -> AsyncIterator[AsyncSession]:
    """Tek kullanimlik (loop'a bagli, havuzsuz) async session saglar.

    Her cagrida yeni engine acilir, is bitince engine dispose edilir. Boylece
    gorev bazli event loop degisiminde baglanti durumu tasinmaz.
    """
    task_engine = create_task_engine(database_url)
    factory = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await task_engine.dispose()


def reset_inherited_engine() -> None:
    """Fork sonrasi ebeveynden miras kalan baglanti havuzunu bosaltir.

    Celery ``worker_process_init`` sinyalinde cagrilir: cocuk surec, ebeveynin
    olusturdugu havuzu (ve varsa baglantilarini) devralmamali.
    """

    async def _dispose() -> None:
        await engine.dispose()

    try:
        asyncio.run(_dispose())
    except Exception:  # pragma: no cover - savunma amacli, is akisini kesmez
        logger.warning("Miras kalan engine dispose edilemedi", exc_info=True)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
