"""Alembic environment.

The database URL is taken from :class:`~graphguard.platform.settings.Settings`, never from
``alembic.ini``. That keeps one source of truth for the connection string and, more importantly,
keeps credentials out of a file that is committed to the repository.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models registers every table on Base.metadata, which is what
# autogenerate diffs against. Without this import the metadata is empty and
# autogenerate would cheerfully emit a migration dropping every table.
from graphguard.platform import models  # noqa: F401
from graphguard.platform.db import Base
from graphguard.platform.settings import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _render_item(type_: str, obj: object, autogen_context: object) -> bool:
    """Ensure rendered column types carry the imports they reference.

    Autogenerate renders our JSON columns as
    ``postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), 'sqlite')`` but does not add an
    import for the bare ``Text``, so the generated migration raises ``NameError`` on execution.
    Registering the import here fixes every future autogeneration rather than requiring a manual
    edit each time. Returning ``False`` keeps Alembic's default rendering.
    """
    if type_ == "type":
        autogen_context.imports.add("from sqlalchemy import Text")  # type: ignore[attr-defined]
    return False


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for review before applying to production."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        render_item=_render_item,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=_render_item,
            # Without compare_type, a column changing from String(64) to String(200)
            # is silently missed by autogenerate.
            compare_type=True,
            # SQLite cannot ALTER most columns; batch mode rewrites the table instead,
            # so the same migration script works on both dialects.
            render_as_batch=get_settings().is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
