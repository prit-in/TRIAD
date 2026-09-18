"""Alembic environment. The database URL always comes from settings, never from alembic.ini."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from triad_core.config import settings
from triad_core.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings().db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# LangGraph owns its checkpoint tables and creates them with `saver.setup()`. They live in
# the same database but are not TRIAD's schema, so autogenerate must leave them alone.
LANGGRAPH_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in LANGGRAPH_TABLES:
        return False
    if type_ == "index" and getattr(obj, "table", None) is not None:
        return obj.table.name not in LANGGRAPH_TABLES
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings().db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
