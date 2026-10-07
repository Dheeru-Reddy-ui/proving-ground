"""Alembic environment: migrations run against DATABASE_URL, online only."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from pg_cli.settings import Settings
from pg_db.models import Base
from pg_db.session import make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = Settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL is not set")
    engine = make_engine(url.get_secret_value())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline (SQL script) migrations are not supported")
run_migrations_online()
