import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresHealthProbe:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ready(self) -> bool:
        try:
            async with asyncio.timeout(3), self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except (TimeoutError, OSError, SQLAlchemyError):
            return False
