"""Alembic's entry point: run the migrations on the database the configuration names."""

import asyncio

from alembic import context
from sqlalchemy import Connection

from analysis_service.sql_store import create_database, engine_for


def _migrate(connection: Connection) -> None:
    # Batch mode, because SQLite has almost no ALTER TABLE.
    context.configure(connection=connection, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run() -> None:
    env = context.config.attributes["env"]
    create_database(env)
    engine = engine_for(env)
    async with engine.connect() as connection:
        await connection.run_sync(_migrate)
    await engine.dispose()


asyncio.run(_run())
